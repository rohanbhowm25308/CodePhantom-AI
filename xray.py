"""Static analysis of a project (dict of path -> text): symbols, import graph, endpoints, duplicates, unused code,
search, retrieval for Q&A and change-impact analysis. Everything here is deterministic — no AI, no guessing."""
import ast
import copy
import hashlib
import json
import posixpath
import re
import sys
from collections import Counter, defaultdict

EXT_LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
            ".java": "Java", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby",
            ".cs": "C#", ".kt": "Kotlin", ".swift": "Swift", ".sql": "SQL", ".sh": "Bash", ".html": "HTML", ".css": "CSS", ".json": "JSON", ".md": "Markdown",
            ".txt": "Text", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML"}
CODE = {"Python", "JavaScript", "TypeScript", "Java", "C", "C++", "Go", "Rust", "PHP", "Ruby", "C#", "Kotlin", "Swift", "Bash"}
BRACE = {"JavaScript", "TypeScript", "Java", "C", "C++", "Go", "Rust", "PHP", "C#", "Kotlin", "Swift"}
STDLIB = set(getattr(sys, "stdlib_module_names", ()))
HTTP = {"get", "post", "put", "delete", "patch", "route"}


def language_of(path):
    return EXT_LANG.get(posixpath.splitext(path)[1].lower(), "Text")


def is_test_path(path):
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*\.py$|_test\.(py|go|js|ts)$|\.(test|spec)\.(js|jsx|ts|tsx)$", path))


def _join(*parts):
    p = posixpath.normpath("/".join(x for x in parts if x))
    return "" if p == "." else p


# ───────────────────────── per-language parsing ─────────────────────────
def _brace_end(lines, start):
    """1-based end line of the block that opens at/after line `start` (1-based). Naive about strings/comments but good enough."""
    depth, seen = 0, False
    for i in range(start - 1, min(len(lines), start + 600)):
        s = re.sub(r"//.*$|#.*$", "", lines[i])
        s = re.sub(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)", '""', s)
        for ch in s:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
                if seen and depth <= 0:
                    return i + 1
        if not seen and i > start and lines[i].strip().endswith(";"):
            return start
    return start


def _cc_text(body):
    return 1 + len(re.findall(r"\b(?:if|for|while|case|catch|elif|except)\b|&&|\|\||\?\s", body))


def _py(path, text):
    info = {"classes": [], "functions": [], "imports": [], "endpoints": [], "refs": defaultdict(set), "error": None}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        info["error"] = f"line {getattr(e, 'lineno', '?')}: {getattr(e, 'msg', e)}"
        return info
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            info["imports"] += [{"module": a.name, "level": 0, "names": []} for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            info["imports"].append({"module": n.module or "", "level": n.level, "names": [a.name for a in n.names]})

    def sig(fn):
        a = fn.args
        pos = a.posonlyargs + a.args
        nd = len(a.defaults)
        params = [{"name": p.arg, "default": i >= len(pos) - nd, "kind": "pos"} for i, p in enumerate(pos)]
        params += [{"name": p.arg, "default": d is not None, "kind": "kwonly"} for p, d in zip(a.kwonlyargs, a.kw_defaults)]
        if a.vararg:
            params.append({"name": "*" + a.vararg.arg, "default": True, "kind": "var"})
        if a.kwarg:
            params.append({"name": "**" + a.kwarg.arg, "default": True, "kind": "var"})
        return params

    def visit(node, stack, cls):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                info["classes"].append({"name": ch.name, "line": ch.lineno, "end": getattr(ch, "end_lineno", ch.lineno), "bases": [ast.unparse(b) for b in ch.bases]})
                visit(ch, stack + [ch.name], ch.name)
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = ".".join(stack + [ch.name])
                calls, cc = set(), 1
                for x in ast.walk(ch):
                    if isinstance(x, ast.Call):
                        f = x.func
                        calls.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
                    if isinstance(x, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp)):
                        cc += 1
                    elif isinstance(x, ast.BoolOp):
                        cc += len(x.values) - 1
                    elif isinstance(x, ast.comprehension):
                        cc += 1 + len(x.ifs)
                    if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                        info["refs"][x.id].add(qn)
                    elif isinstance(x, ast.Attribute):
                        info["refs"][x.attr].add(qn)
                decos = [ast.unparse(d) for d in ch.decorator_list]
                rec = {"name": ch.name, "qualname": qn, "kind": "method" if cls else "function", "cls": cls, "line": ch.lineno,
                       "end": getattr(ch, "end_lineno", ch.lineno), "params": sig(ch), "decorators": decos, "calls": sorted(c for c in calls if c),
                       "cc": cc, "returns": ast.unparse(ch.returns) if ch.returns else ""}
                info["functions"].append(rec)
                for d in ch.decorator_list:
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in HTTP and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                        methods = [d.func.attr.upper()]
                        if d.func.attr == "route":
                            methods = ["GET"]
                            for kw in d.keywords:
                                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                                    methods = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
                        info["endpoints"].append({"methods": methods, "path": d.args[0].value, "handler": qn, "line": ch.lineno})
                visit(ch, stack + [ch.name], None)
            else:
                visit(ch, stack, cls)

    visit(tree, [], None)
    for x in ast.walk(tree):                                   # module-level references (outside any function)
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
            info["refs"][x.id].add("<module>")
        elif isinstance(x, ast.Attribute):
            info["refs"][x.attr].add("<module>")
    return info


JS_FN = [re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"),
         re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b[^(]*\(([^)]*)\)|\(([^)]*)\)\s*=>|([A-Za-z_$][\w$]*)\s*=>)")]
GENERIC = {
    "Java": (r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)+[\w<>\[\],.? ]+\s+(\w+)\s*\(([^)]*)\)\s*(?:throws [\w., ]+)?\s*\{?", r"^\s*(?:public\s+|abstract\s+|final\s+)*(?:class|interface|enum)\s+(\w+)", r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;"),
    "Go": (r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)", r"^type\s+(\w+)\s+(?:struct|interface)", r"^\s*(?:import\s+)?\"([\w./-]+)\""),
    "Rust": (r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", r"^\s*(?:pub\s+)?(?:use|mod)\s+([\w:]+)"),
    "PHP": (r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)\s*\(([^)]*)\)", r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", r"(?:require|include)(?:_once)?\s*\(?\s*['\"]([^'\"]+)['\"]"),
    "Ruby": (r"^\s*def\s+(?:self\.)?(\w+[?!]?)\s*\(?([^)\n]*)", r"^\s*class\s+(\w+)", r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]"),
    "C": (r"^[A-Za-z_][\w\s\*&:<>,]*?\s+\**(\w+)\s*\(([^;{)]*)\)\s*\{?\s*$", r"^\s*(?:typedef\s+)?struct\s+(\w+)", r"^\s*#\s*include\s+\"([^\"]+)\""),
    "C#": (r"^\s*(?:public|private|protected|internal|static|virtual|override|async|\s)+[\w<>\[\],.? ]+\s+(\w+)\s*\(([^)]*)\)\s*\{?", r"^\s*(?:public\s+|internal\s+|static\s+|abstract\s+)*(?:class|interface|struct|enum)\s+(\w+)", r"^\s*using\s+([\w.]+)\s*;"),
    "Kotlin": (r"^\s*(?:private\s+|public\s+|internal\s+|override\s+|suspend\s+)*fun\s+(?:<[^>]*>\s*)?(?:\w+\.)?(\w+)\s*\(([^)]*)\)", r"^\s*(?:data\s+|open\s+|abstract\s+|sealed\s+)*(?:class|interface|object)\s+(\w+)", r"^\s*import\s+([\w.]+)"),
    "Swift": (r"^\s*(?:public\s+|private\s+|static\s+|override\s+)*func\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)", r"^\s*(?:public\s+|final\s+)*(?:class|struct|enum|protocol)\s+(\w+)", r"^\s*import\s+(\w+)"),
    "Bash": (r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{?", r"(?!x)x(\w)", r"^\s*(?:source|\.)\s+([^\s;]+)"),
}
GENERIC["C++"] = GENERIC["C"]
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "function", "sizeof", "do"}


def _clike(path, text, lang):
    lines = text.split("\n")
    info = {"classes": [], "functions": [], "imports": [], "endpoints": [], "refs": {}, "error": None}
    fre, cre, ire = GENERIC.get(lang) or (None, None, None)
    in_js = lang in ("JavaScript", "TypeScript")
    cls_stack = []
    for i, ln in enumerate(lines, 1):
        if in_js:
            m = re.search(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", ln)
            if m:
                info["classes"].append({"name": m.group(1), "line": i, "end": _brace_end(lines, i), "bases": []})
            for r in re.finditer(r"""import\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""", ln):
                info["imports"].append({"module": r.group(1) or r.group(2), "level": 0, "names": []})
            em = re.search(r"\b(app|router|server)\.(get|post|put|delete|patch)\(\s*['\"`]([^'\"`]+)['\"`]", ln)
            if em:
                info["endpoints"].append({"methods": [em.group(2).upper()], "path": em.group(3), "handler": f"{em.group(1)}.{em.group(2)}", "line": i})
            for k, rx in enumerate(JS_FN):
                m = rx.match(ln)
                if m:
                    params = next((g for g in m.groups()[1:] if g is not None), "") if k == 0 else (m.group(2) or m.group(3) or m.group(4) or "")
                    end = _brace_end(lines, i) if "{" in ln or not ln.rstrip().endswith(";") else i
                    info["functions"].append({"name": m.group(1), "qualname": m.group(1), "kind": "function", "cls": None, "line": i, "end": end,
                                              "params": [{"name": p.strip().split("=")[0].strip(), "default": "=" in p, "kind": "pos"} for p in params.split(",") if p.strip()],
                                              "decorators": [], "calls": [], "cc": _cc_text("\n".join(lines[i - 1:end])), "returns": ""})
                    break
            else:
                m = re.match(r"^\s+(?:async\s+)?(?:static\s+)?([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{", ln)
                if m and m.group(1) not in KEYWORDS and info["classes"]:
                    end = _brace_end(lines, i)
                    info["functions"].append({"name": m.group(1), "qualname": m.group(1), "kind": "method", "cls": info["classes"][-1]["name"], "line": i, "end": end,
                                              "params": [{"name": p.strip(), "default": False, "kind": "pos"} for p in m.group(2).split(",") if p.strip()],
                                              "decorators": [], "calls": [], "cc": _cc_text("\n".join(lines[i - 1:end])), "returns": ""})
        elif fre:
            if cre:
                m = re.match(cre, ln)
                if m and m.lastindex:
                    info["classes"].append({"name": m.group(1), "line": i, "end": _brace_end(lines, i) if lang in BRACE else i, "bases": []})
            m = re.match(fre, ln)
            if m and m.group(1) not in KEYWORDS and not ln.rstrip().endswith(";"):
                end = _brace_end(lines, i) if lang in BRACE else i
                info["functions"].append({"name": m.group(1), "qualname": m.group(1), "kind": "function", "cls": None, "line": i, "end": end,
                                          "params": [{"name": p.strip(), "default": "=" in p, "kind": "pos"} for p in (m.group(2) if m.lastindex and m.lastindex >= 2 else "").split(",") if p.strip()],
                                          "decorators": [], "calls": [], "cc": _cc_text("\n".join(lines[i - 1:end])), "returns": ""})
            for r in re.finditer(ire, ln) if ire else []:
                info["imports"].append({"module": r.group(1), "level": 0, "names": []})
    return info


def _parse(path, text):
    lang = language_of(path)
    if lang == "Python":
        return _py(path, text)
    if lang in CODE:
        return _clike(path, text, lang)
    return {"classes": [], "functions": [], "imports": [], "endpoints": [], "refs": {}, "error": None}


# ───────────────────────── imports → file graph ─────────────────────────
def _resolve(path, lang, imp, paths):
    base = posixpath.dirname(path)
    out = set()
    mod = imp["module"]
    if lang == "Python":
        if imp["level"]:
            d = base
            for _ in range(imp["level"] - 1):
                d = posixpath.dirname(d)
            roots = [_join(d, mod.replace(".", "/"))]
        else:
            roots = [_join(mod.replace(".", "/")), _join(base, mod.replace(".", "/"))]
        for r in roots:
            for c in (r + ".py", _join(r, "__init__.py")):
                if c in paths:
                    out.add(c)
            for nm in imp["names"]:
                for c in (_join(r, nm) + ".py", _join(r, nm, "__init__.py")):
                    if c in paths:
                        out.add(c)
    elif lang in ("JavaScript", "TypeScript"):
        if mod.startswith("."):
            r = _join(base, mod)
            for c in [r] + [r + e for e in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json")] + [_join(r, "index" + e) for e in (".js", ".ts", ".jsx", ".tsx")]:
                if c in paths:
                    out.add(c)
                    break
    elif lang in ("C", "C++"):
        for c in (_join(base, mod), _join(mod)):
            if c in paths:
                out.add(c)
    elif lang == "Java":
        tail = mod.replace(".", "/") + ".java"
        out |= {p for p in paths if p.endswith("/" + tail) or p == tail}
    elif lang in ("PHP", "Ruby", "Bash"):
        ext = {"PHP": ".php", "Ruby": ".rb", "Bash": ".sh"}[lang]
        for c in (_join(base, mod), _join(base, mod + ext), _join(mod), _join(mod + ext)):
            if c in paths:
                out.add(c)
                break
    out.discard(path)
    return out


def _external(lang, imp):
    m = imp["module"]
    if lang == "Python":
        if imp["level"]:
            return None
        top = m.split(".")[0]
        return None if (not top or top in STDLIB) else top
    if lang in ("JavaScript", "TypeScript"):
        if m.startswith(".") or m.startswith("node:"):
            return None
        parts = m.split("/")
        return "/".join(parts[:2]) if m.startswith("@") else parts[0]
    return None


def build_index(files):
    paths = set(files)
    idx = {"files": {}, "functions": [], "classes": [], "edges": [], "external": {}, "endpoints": [], "errors": [], "refs": {}}
    ext = defaultdict(set)
    parsed = {}
    for path, text in files.items():
        lang = language_of(path)
        try:
            info = _parse(path, text)
        except Exception as e:  # noqa: BLE001 — one odd file must never break the whole analysis
            info = {"classes": [], "functions": [], "imports": [], "endpoints": [], "refs": {}, "error": f"could not analyse: {type(e).__name__}"}
        parsed[path] = info
        idx["files"][path] = {"path": path, "lang": lang, "lines": text.count("\n") + 1, "size": len(text), "test": is_test_path(path),
                              "functions": len(info["functions"]), "classes": len(info["classes"]), "error": info["error"]}
        if info["error"]:
            idx["errors"].append({"path": path, "error": info["error"]})
        for f in info["functions"]:
            idx["functions"].append({**f, "path": path})
        for c in info["classes"]:
            idx["classes"].append({**c, "path": path})
        for e in info["endpoints"]:
            idx["endpoints"].append({**e, "path": path})
        idx["refs"][path] = {k: sorted(v) for k, v in info["refs"].items()} if isinstance(info["refs"], dict) else {}
        for imp in info["imports"]:
            res = _resolve(path, lang, imp, paths)
            subs = {r for r in res if not r.endswith("__init__.py")}
            if imp["names"] and subs:
                res = subs                               # "from pkg import module": the edge goes to the module, not the package
            for t in res:
                idx["edges"].append({"s": path, "t": t})
            e = None if res else _external(lang, imp)
            if e:
                ext[e].add(path)
    seen = set()
    idx["edges"] = [e for e in idx["edges"] if (e["s"], e["t"]) not in seen and not seen.add((e["s"], e["t"]))]
    deg_in, deg_out = defaultdict(int), defaultdict(int)
    for e in idx["edges"]:
        deg_out[e["s"]] += 1
        deg_in[e["t"]] += 1
    for p, f in idx["files"].items():
        f["in"], f["out"] = deg_in[p], deg_out[p]
    idx["external"] = {k: sorted(v) for k, v in sorted(ext.items())}
    return idx


# ───────────────────────── duplicates / unused ─────────────────────────
def _norm_hash_node(node):
    """Structure hash of a Python function: local variable names and the docstring do not matter."""
    node = copy.deepcopy(node)
    local = {a.arg for a in node.args.args + node.args.posonlyargs + node.args.kwonlyargs}
    local |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    names = {}

    class T(ast.NodeTransformer):
        def visit_Name(self, n):
            if n.id in local:
                n.id = names.setdefault(n.id, f"v{len(names)}")
            return n

        def visit_arg(self, n):
            n.arg = names.setdefault(n.arg, f"v{len(names)}")
            n.annotation = None
            return n
    node.name, node.decorator_list, node.returns = "f", [], None
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
        node.body = node.body[1:] or [ast.Pass()]
    T().visit(node)
    return hashlib.sha1(ast.dump(node).encode()).hexdigest()


def _norm_lines(lines, a, b):
    out = []
    for ln in lines[a - 1:b]:
        s = re.sub(r"\s+", " ", re.sub(r"(//|#).*$", "", ln)).strip()
        if s and s not in ("{", "}", "};"):
            out.append(s)
    return out


def duplicates(files, idx):
    lines = {p: t.split("\n") for p, t in files.items()}                 # split every file once
    funcs = [f for f in idx["functions"] if f["end"] - f["line"] >= 4 and not idx["files"][f["path"]]["test"]][:900]
    groups, used = [], set()
    by_hash, pymap = defaultdict(list), {}
    for f in funcs:
        if not f["path"].endswith(".py"):
            continue
        if f["path"] not in pymap:                                         # parse every Python file once
            try:
                pymap[f["path"]] = {(n.lineno, n.name): n for n in ast.walk(ast.parse(files[f["path"]])) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            except SyntaxError:
                pymap[f["path"]] = {}
        node = pymap[f["path"]].get((f["line"], f["name"]))
        if node is not None:
            by_hash[_norm_hash_node(node)].append(f)
    for h, items in by_hash.items():
        if len(items) > 1:
            groups.append({"kind": "exact", "similarity": 1.0, "items": [{"path": i["path"], "name": i["qualname"], "start": i["line"], "end": i["end"]} for i in items]})
            used |= {(i["path"], i["line"]) for i in items}
    sh = {}
    for f in funcs:
        if (f["path"], f["line"]) in used:
            continue
        ln = _norm_lines(lines[f["path"]], f["line"], f["end"])
        if len(ln) >= 6:
            sh[(f["path"], f["line"])] = (f, {tuple(ln[i:i + 3]) for i in range(len(ln) - 2)})
    inv = defaultdict(set)                                                 # shingle -> functions: only compare functions that share something
    for k, (_, sset) in sh.items():
        for g in sset:
            inv[g].add(k)
    cand = defaultdict(int)
    for ks in inv.values():
        if 1 < len(ks) <= 12:
            ks = sorted(ks)
            for i in range(len(ks)):
                for j in range(i + 1, len(ks)):
                    cand[(ks[i], ks[j])] += 1
    for (ka, kb), _n in cand.items():
        (fa, sa), (fb, sb) = sh[ka], sh[kb]
        if fa["path"] == fb["path"] and abs(fa["line"] - fb["line"]) < 3:
            continue
        inter = len(sa & sb)
        if inter and inter / len(sa | sb) >= 0.75:
            groups.append({"kind": "near", "similarity": round(inter / len(sa | sb), 2),
                           "items": [{"path": x["path"], "name": x["qualname"], "start": x["line"], "end": x["end"]} for x in (fa, fb)]})
    seen = defaultdict(list)
    for p, t in files.items():
        if t.strip() and len(t) > 120 and language_of(p) in CODE:
            seen[hashlib.sha1(t.encode()).hexdigest()].append(p)
    for ps in seen.values():
        if len(ps) > 1:
            groups.append({"kind": "file", "similarity": 1.0, "items": [{"path": p, "name": p, "start": 1, "end": files[p].count("\n") + 1} for p in ps]})
    return sorted(groups, key=lambda g: -g["similarity"])[:40]


def unused(files, idx):
    ref_index = defaultdict(set)                                           # name -> {(file, enclosing function)}, built once
    for p, d in idx["refs"].items():
        for name, qs in d.items():
            for q in qs:
                ref_index[name].add((p, q))
    counts = Counter()                                                     # identifier occurrences in non-Python code, counted once
    for p, t in files.items():
        if not p.endswith(".py") and language_of(p) in CODE:
            counts.update(re.findall(r"[A-Za-z_$][\w$]*", t))
    out = []
    for f in idx["functions"]:
        name, path = f["name"], f["path"]
        if name.startswith("__") or name in ("main", "setUp", "tearDown", "run") or name.startswith("test") or idx["files"][path]["test"]:
            continue
        if path.endswith(".py"):
            used = any(not (p == path and q == f["qualname"]) for p, q in ref_index.get(name, ()))
        else:
            used = counts[name] > 1
        if used:
            continue
        note = ""
        if f["decorators"]:
            note = "It is decorated — a framework (routes, CLI, signals) may call it without a visible reference."
        if f["kind"] == "method":
            note = (note + " " if note else "") + "Methods can be called dynamically or by subclasses."
        out.append({"path": path, "name": f["qualname"], "line": f["line"], "kind": f["kind"], "why": "No other code in the project references it.",
                    "caveat": note or "Check for dynamic use (getattr, reflection, tests outside the project) before deleting."})
    return out[:60]


# ───────────────────────── search + retrieval ─────────────────────────
def _tokens(q):
    q = re.sub(r"([a-z])([A-Z])", r"\1 \2", q)
    return [t for t in re.split(r"[^A-Za-z0-9]+", q.lower()) if len(t) > 2 and t not in {"the", "and", "how", "where", "does", "what", "for", "with", "that", "this", "are", "from", "into", "when", "which"}]


def search(files, idx, q, limit=12):
    toks = _tokens(q)
    if not toks:
        return []
    hits = []
    lc = {p: t.split("\n") for p, t in files.items()}
    for f in idx["functions"]:
        n = (f["qualname"] + " " + re.sub(r"([a-z])([A-Z])", r"\1 \2", f["name"])).lower().replace("_", " ")
        score, why = 0, []
        for t in toks:
            if t in n or (len(t) >= 6 and t[:5] in n):          # "computed" ≈ compute
                score += 6
                why.append(f"name matches “{t}”")
        body = "\n".join(lc[f["path"]][f["line"] - 1:f["end"]]).lower()
        for t in toks:
            c = body.count(t)
            if c:
                score += min(c, 4)
                why.append(f"“{t}” ×{c} in the body")
        if any(t in f["path"].lower() for t in toks):
            score += 4
            why.append("file path matches")
        if score >= 4:
            lines = lc[f["path"]]
            hits.append({"path": f["path"], "name": f["qualname"], "kind": f["kind"], "line": f["line"], "end": f["end"], "score": score, "why": why[:4],
                         "snippet": "\n".join(lines[f["line"] - 1:min(f["end"], f["line"] + 5)])})
    for p, text in files.items():
        if language_of(p) in CODE or p.lower().endswith(".md"):
            low = text.lower()
            c = sum(low.count(t) for t in toks)
            nm = sum(2 for t in toks if t in p.lower())
            if c + nm >= 3 and not any(h["path"] == p for h in hits):
                ln = next((i for i, l in enumerate(text.split("\n"), 1) if any(t in l.lower() for t in toks)), 1)
                lines = text.split("\n")
                hits.append({"path": p, "name": p, "kind": "file", "line": ln, "end": ln, "score": min(c, 8) + nm, "why": [f"{c} keyword matches"],
                             "snippet": "\n".join(lines[max(0, ln - 2):ln + 3])})
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def retrieve(files, idx, question, budget=22000):
    """Pick the most relevant code for a question. Returns (context_text, sources)."""
    toks = _tokens(question)
    scored = []
    for p, text in files.items():
        if language_of(p) not in CODE and not p.lower().endswith((".md", ".json", ".txt", ".toml", ".yml", ".yaml")):
            continue
        low = text.lower()
        s = sum(min(low.count(t), 6) for t in toks) + 3 * sum(t in p.lower() for t in toks) + (2 if p.lower().startswith("readme") else 0)
        s += 1 if p in ("app.py", "main.py", "index.js", "server.js", "manage.py") else 0
        scored.append((s, p))
    scored.sort(reverse=True)
    ctx, sources, used = [], [], 0
    tree = "\n".join(sorted(files))[:1500]
    ctx.append(f"PROJECT FILES:\n{tree}\n")
    for s, p in scored[:8]:
        if s <= 0 and sources:
            break
        text, lines = files[p], files[p].split("\n")
        funcs = [f for f in idx["functions"] if f["path"] == p and any(t in f["name"].lower() for t in toks)]
        if funcs:
            chunks = [(f["line"], min(f["end"], f["line"] + 70)) for f in funcs[:4]]
        else:
            chunks = [(1, min(len(lines), 80))]
        for a, b in chunks:
            body = "\n".join(f"{i + a}: {l}" for i, l in enumerate(lines[a - 1:b]))
            if used + len(body) > budget:
                break
            ctx.append(f"--- {p} (lines {a}-{b}) ---\n{body}")
            used += len(body)
            sources.append({"path": p, "start": a, "end": b})
    return "\n".join(ctx), sources


# ───────────────────────── change impact ─────────────────────────
def _reverse(idx):
    rev = defaultdict(set)
    for e in idx["edges"]:
        rev[e["t"]].add(e["s"])
    return rev


def _closure(start, rev):
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        for s in rev.get(cur, ()):
            if s not in seen:
                seen.add(s)
                todo.append(s)
    return seen


def signature_changes(old_params, new_params):
    out = []
    old = [p["name"] for p in old_params]
    new = [p["name"] for p in new_params]
    for p in old_params:
        if p["name"] not in new:
            idx = old.index(p["name"])
            if idx < len(new) and new[idx] not in old:
                out.append({"type": "renamed", "detail": f"parameter {p['name']} was renamed to {new[idx]} — keyword callers break"})
            else:
                out.append({"type": "removed", "detail": f"parameter {p['name']} was removed"})
    for p in new_params:
        if p["name"] not in old and not p["default"]:
            out.append({"type": "added_required", "detail": f"new required parameter {p['name']} — every existing caller must pass it"})
    common_old = [n for n in old if n in new]
    if common_old != [n for n in new if n in old]:
        out.append({"type": "reordered", "detail": "parameters were reordered — positional callers break"})
    for p in old_params:
        q = next((x for x in new_params if x["name"] == p["name"]), None)
        if q and p["default"] and not q["default"]:
            out.append({"type": "default_removed", "detail": f"parameter {p['name']} no longer has a default"})
    return out


def impact(files, idx, path, func=None, new_code=None, project_tests=None, entry=None):
    if path not in files:
        raise ValueError("That file is not in the project.")
    rev = _reverse(idx)
    dependents = _closure(path, rev)
    target = next((f for f in idx["functions"] if f["path"] == path and (f["qualname"] == func or f["name"] == func)), None) if func else None
    if func and not target:
        raise ValueError(f"Function {func} was not found in {path}.")
    callers, approx = [], False
    if target:
        name = target["name"]
        scope = dependents | {path}
        if path.endswith(".py"):
            for f in idx["functions"]:
                if f["path"] in scope and name in f["calls"] and not (f["path"] == path and f["qualname"] == target["qualname"]):
                    callers.append({"path": f["path"], "name": f["qualname"], "line": f["line"]})
        else:
            approx = True
            for p in scope:
                for i, l in enumerate(files[p].split("\n"), 1):
                    if re.search(r"\b" + re.escape(name) + r"\s*\(", l) and not (p == path and i == target["line"]):
                        callers.append({"path": p, "name": l.strip()[:60], "line": i})
    # endpoints reachable from the change
    caller_names = {c["name"] for c in callers}
    endpoints = []
    for e in idx["endpoints"]:
        hit = (target and e["path"] == path and (e["handler"] == target["qualname"])) or e["handler"] in caller_names or (not target and (e["path"] == path or e["path"] in dependents))
        if hit:
            endpoints.append(e)
    # tests that exercise it
    tests = []
    for p, f in idx["files"].items():
        if f["test"] and (p in dependents or (target and re.search(r"\b" + re.escape(target["name"]) + r"\b", files[p])) or posixpath.splitext(posixpath.basename(path))[0] in files[p]):
            tests.append({"path": p, "reason": "imports/mentions the changed code"})
    recorded = bool(project_tests) and entry and (entry == path or entry in dependents)
    # API breaking changes (Python signature comparison)
    breaking = []
    if target and new_code and path.endswith(".py"):
        try:
            tree = ast.parse(new_code)
            newf = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        except SyntaxError as e:
            raise ValueError(f"The proposed code has a syntax error (line {e.lineno}).")
        if not newf:
            raise ValueError("The proposed code must contain a function definition.")
        tmp = _py("tmp.py", ast.unparse(newf))
        nf = tmp["functions"][0]
        if nf["name"] != target["name"]:
            breaking.append({"type": "renamed_function", "detail": f"function renamed {target['name']} → {nf['name']} — all {len(callers)} caller(s) break"})
        breaking += signature_changes(target["params"], nf["params"])
        if target["returns"] != nf["returns"] and (target["returns"] or nf["returns"]):
            breaking.append({"type": "return_type", "detail": f"return annotation changed {target['returns'] or '(none)'} → {nf['returns'] or '(none)'}"})
    # risk model: every factor shows its evidence
    factors = []

    def add(name, pts, ev):
        factors.append({"name": name, "points": pts, "evidence": ev})
    nc = len(callers)
    if nc:
        add(f"{nc} direct caller(s)", 3 if nc > 5 else 2 if nc > 2 else 1, [f"{c['path']}:{c['line']} {c['name']}" for c in callers[:6]])
    if dependents:
        add(f"{len(dependents)} file(s) depend on this file", 3 if len(dependents) > 5 else 2 if len(dependents) > 2 else 1, sorted(dependents)[:6])
    if endpoints:
        add(f"{len(endpoints)} public endpoint(s) affected", 2 * min(len(endpoints), 2), [f"{'/'.join(e['methods'])} {e['path']} ({e['path'] if False else e['handler']})" for e in endpoints[:6]])
    if not tests and not recorded:
        add("no test covers this code", 2, ["no test file mentions it and no recorded project test reaches it"])
    if target:
        if target["cc"] > 10:
            add(f"high complexity (cyclomatic {target['cc']})", 2, [f"{path}:{target['line']}-{target['end']}"])
        elif target["cc"] > 5:
            add(f"moderate complexity (cyclomatic {target['cc']})", 1, [f"{path}:{target['line']}-{target['end']}"])
        if target["end"] - target["line"] > 60:
            add(f"long function ({target['end'] - target['line'] + 1} lines)", 1, [f"{path}:{target['line']}-{target['end']}"])
    if breaking:
        add(f"{len(breaking)} breaking API change(s)", min(3 * len(breaking), 6), [b["detail"] for b in breaking])
    score = sum(f["points"] for f in factors)
    level = "low" if score <= 2 else "medium" if score <= 5 else "high"
    return {"path": path, "function": target["qualname"] if target else None, "level": level, "score": score, "factors": factors, "callers": callers, "approximate_callers": approx,
            "dependents": sorted(dependents), "endpoints": endpoints, "tests": tests, "recorded_tests_reach": bool(recorded), "breaking": breaking,
            "affected": sorted(dependents | {path}), "note": "Static analysis: dynamic calls (getattr, reflection, string-based routing) are not visible." + (" Callers are matched by name for this language (approximate)." if approx else "")}


# ───────────────────────── conventions / dependencies (memory suggestions) ─────────────────────────
def conventions(files, idx):
    out = []
    py = [f for f in idx["functions"] if f["path"].endswith(".py") and f["kind"] == "function" and not f["name"].startswith("__")]
    js = [f for f in idx["functions"] if f["path"].endswith((".js", ".jsx", ".ts", ".tsx"))]
    snake = sum(1 for f in py + js if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)+", f["name"]))
    camel = sum(1 for f in py + js if re.fullmatch(r"[a-z]+[A-Z][A-Za-z0-9]*", f["name"]))
    if snake + camel >= 3:
        style = "snake_case" if snake >= camel else "camelCase"
        out.append({"kind": "convention", "text": f"Function names use {style} ({max(snake, camel)} of {snake + camel} multi-word names).", "evidence": f"counted over {len(py + js)} functions"})
    pas = sum(1 for c in idx["classes"] if re.fullmatch(r"[A-Z][A-Za-z0-9]+", c["name"]))
    if pas >= 2:
        out.append({"kind": "convention", "text": "Class names use PascalCase.", "evidence": f"{pas} classes"})
    langs = defaultdict(int)
    for p, f in idx["files"].items():
        if f["lang"] in CODE:
            langs[f["lang"]] += f["lines"]
    if langs:
        top = max(langs, key=langs.get)
        out.append({"kind": "architecture", "text": f"Primary language: {top} ({langs[top]} lines of {sum(langs.values())}).", "evidence": "line counts"})
    entries = [p for p in files if p in ("app.py", "main.py", "manage.py", "index.js", "server.js", "main.js", "src/index.js", "src/main.py")]
    if entries:
        out.append({"kind": "architecture", "text": f"Entry point: {entries[0]}.", "evidence": "conventional file name"})
    if idx["endpoints"]:
        out.append({"kind": "architecture", "text": f"HTTP API with {len(idx['endpoints'])} endpoint(s), defined in {', '.join(sorted({e['path'] for e in idx['endpoints']})[:3])}.", "evidence": "route decorators / handlers"})
    tests = [p for p, f in idx["files"].items() if f["test"]]
    if tests:
        fw = "pytest" if any("pytest" in files[t] for t in tests) else "unittest" if any("unittest" in files[t] for t in tests) else "jest" if any(re.search(r"\b(describe|it|test)\(", files[t]) for t in tests) else "an unidentified framework"
        out.append({"kind": "convention", "text": f"Tests live in {', '.join(tests[:3])} and use {fw}.", "evidence": f"{len(tests)} test file(s)"})
    deps = []
    if "requirements.txt" in files:
        deps += [re.split(r"[<>=!~\[;\s]", l.strip())[0] for l in files["requirements.txt"].split("\n") if l.strip() and not l.startswith(("#", "-"))]
    if "package.json" in files:
        try:
            j = json.loads(files["package.json"])
            deps += list((j.get("dependencies") or {}).keys())
        except ValueError:
            pass
    if deps:
        out.append({"kind": "dependency", "text": "Main dependencies: " + ", ".join(deps[:12]) + ".", "evidence": "requirements.txt / package.json"})
    elif idx["external"]:
        out.append({"kind": "dependency", "text": "Imported third-party packages: " + ", ".join(list(idx["external"])[:12]) + ".", "evidence": "import statements"})
    return out


def graph(idx):
    return {"nodes": [{"id": p, "lang": f["lang"], "lines": f["lines"], "functions": f["functions"], "classes": f["classes"], "in": f.get("in", 0), "out": f.get("out", 0), "test": f["test"]}
                      for p, f in idx["files"].items() if f["lang"] in CODE or f.get("in") or f.get("out")], "edges": idx["edges"]}


def summary(idx):
    langs = defaultdict(int)
    for f in idx["files"].values():
        langs[f["lang"]] += 1
    return {"files": len(idx["files"]), "lines": sum(f["lines"] for f in idx["files"].values()), "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
            "functions": len(idx["functions"]), "classes": len(idx["classes"]), "edges": len(idx["edges"]), "endpoints": len(idx["endpoints"]),
            "external": len(idx["external"]), "parse_errors": len(idx["errors"])}
