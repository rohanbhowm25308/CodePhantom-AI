"""Labs that work on the code in the editor: Proof Mode, Review Tribunal, Spec-to-Test, Merge Resolver and the
Security Attack Simulator. Every result is tagged as verified (executed/observed), static analysis, or AI prediction."""
import ast
import difflib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from flask import Blueprint, jsonify, request

import accounts
import sandbox
from groq_client import complete, extract_json
from streamutil import ApiError, ev, item, ndjson

bp = Blueprint("labs", __name__)
bp.before_request(accounts._csrf)
allowed = bump = None            # injected by app.py
MAX_CODE = 24_000


def _key():
    k = os.getenv("GROQ_API_KEY")
    if not k:
        raise ApiError("Server has no GROQ_API_KEY.", 503)
    return k


def _gate(kind="ai", cost=1):
    msg = allowed(kind, cost)
    if msg:
        raise ApiError(msg, 429)


def _code_body():
    d = request.get_json(silent=True) or {}
    code, lang = d.get("code"), str(d.get("language", "Python"))[:30]
    if not isinstance(code, str) or not code.strip():
        raise ApiError("Put some code in the editor first.")
    if len(code) > MAX_CODE:
        raise ApiError(f"The code is too long for this tool (limit {MAX_CODE} characters).")
    return d, code, lang


def norm(s):
    return "\n".join(l.rstrip() for l in str(s).replace("\r\n", "\n").split("\n")).strip()


def numbered(code):
    return "\n".join(f"{i}: {l}" for i, l in enumerate(code.split("\n"), 1))


def _run(lang, code, stdin=""):
    try:
        return sandbox.execute(lang, code, stdin)
    except sandbox.SandboxError as e:
        raise ApiError(str(e), 503)


def _need_runner(lang):
    if not sandbox.backend() or lang.lower() not in sandbox.languages():
        raise ApiError(f"Running {lang} is not enabled on this server (Docker, Judge0 or the local runner is needed).", 503)


# ═════════════════════ Phantom Proof Mode ═════════════════════
PROOF_SYS = ("You design tests for a program. Reply with ONE JSON object: {\"mode\": \"io\"|\"unit\", \"tests\": [{\"name\": string, \"category\": \"normal\"|\"boundary\"|\"edge\", "
             "\"input\": string (exact stdin; io mode), \"expected\": string (exact stdout; io mode), \"snippet\": string (unit mode: code appended after the program that uses assert "
             "and fails if the behaviour is wrong), \"note\": string}]}. Use \"io\" when the program reads standard input and prints its result. Use \"unit\" only for Python or "
             "JavaScript code that defines functions or classes without reading input. Write 6 to 9 tests covering normal, boundary and edge cases. Work out expected outputs carefully.")
FIX_SYS = ("A program was tested and some tests failed. For each failing test decide whether the CODE is wrong or the test's EXPECTATION is wrong. Reply with ONE JSON object: "
           "{\"verdicts\": [{\"test\": string, \"cause\": \"code_bug\"|\"wrong_expectation\"|\"unclear\", \"explanation\": string}], \"fixed_code\": string (the complete corrected "
           "program, or \"\" if the code is fine), \"summary\": string}. Change the code as little as possible.")


def run_defined_tests(lang, code, mode, tests):
    """Execute a list of test definitions for real. Returns (results, compile_error|None)."""
    results = []
    for i, t in enumerate(tests, 1):
        if mode == "unit":
            r = _run(lang, code.rstrip() + "\n\n" + str(t.get("snippet", "")) + "\n", "")
            passed, actual = r["status"] == "ok", (r["stdout"] or r["stderr"])
        else:
            r = _run(lang, code, str(t.get("input", "")))
            passed, actual = r["status"] == "ok" and norm(r["stdout"]) == norm(t.get("expected", "")), r["stdout"]
        if r["status"] == "compile_error":
            return [], r
        results.append({"index": i, "name": str(t.get("name") or f"Test {i}")[:80], "category": t.get("category", "normal"), "mode": mode, "input": str(t.get("input", ""))[:600],
                        "expected": str(t.get("expected", ""))[:600], "snippet": str(t.get("snippet", ""))[:800], "actual": str(actual)[:800], "stderr": r["stderr"][:600],
                        "status": r["status"], "passed": passed, "time_ms": r.get("time_ms"), "note": str(t.get("note", ""))[:200]})
    return results, None


def _udiff(a, b, name="code"):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{name}", f"b/{name}", n=2))[:12000]


@bp.route("/api/labs/proof", methods=["POST"])
def proof():
    d, code, lang = _code_body()
    desc = str(d.get("description", ""))[:1500]
    key = _key()
    _gate("ai", 2)
    _need_runner(lang)
    _gate("run", 18)

    def gen():
        yield {"step": "tests", "msg": "Designing normal, boundary and edge-case tests…"}
        txt, err = complete(key, PROOF_SYS, f"Language: {lang}\nRequirement: {desc or '(infer it from the code)'}\n\nProgram:\n{code}", max_tokens=3500, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        mode = "unit" if obj.get("mode") == "unit" and lang.lower() in ("python", "javascript") else "io"
        tests = [t for t in (obj.get("tests") or []) if isinstance(t, dict)][:9]
        tests = [t for t in tests if (mode == "unit" and t.get("snippet")) or (mode == "io" and "input" in t and "expected" in t)]
        if not tests:
            raise ApiError("The AI did not produce usable tests. Try again, or describe the requirement.", 502)
        yield {"step": "run", "msg": f"Running {len(tests)} tests in the sandbox…"}
        results, cerr = run_defined_tests(lang, code, mode, tests)
        rounds = [{"label": "Original code", "results": results, "compile_error": cerr}]
        verdicts, fixed, summary, fixdiff = [], "", "", ""
        failing = [r for r in results if not r["passed"]]
        if cerr or failing:
            yield {"step": "fix", "msg": "Some tests disagree — asking who is wrong, the code or the expectation…"}
            ftxt, ferr = complete(key, FIX_SYS, json.dumps({"language": lang, "requirement": desc, "code": code, "failing": failing[:6] or [{"compile_error": cerr["stderr"][:800]}]})[:14000], max_tokens=4000, json_mode=True)
            if not ferr:
                fo = extract_json(ftxt) or {}
                verdicts = [v for v in (fo.get("verdicts") or []) if isinstance(v, dict)][:8]
                summary = str(fo.get("summary", ""))[:500]
                fixed = fo.get("fixed_code") if isinstance(fo.get("fixed_code"), str) else ""
            if fixed.strip() and norm(fixed) != norm(code) and len(fixed) <= MAX_CODE:
                yield {"step": "rerun", "msg": "Re-running every test on the fixed code…"}
                r2, c2 = run_defined_tests(lang, fixed, mode, tests)
                rounds.append({"label": "Fixed code", "results": r2, "compile_error": c2})
                fixdiff = _udiff(code, fixed)
            else:
                fixed = ""
        final = rounds[-1]
        passed = sum(1 for r in final["results"] if r["passed"])
        disagree = [{"test": r["name"], "expected": r["expected"], "actual": r["actual"]} for r in rounds[0]["results"] if r["mode"] == "io" and r["status"] == "ok" and not r["passed"]]
        evidence = [ev(f"{sum(1 for r in results if r['passed'])}/{len(results)} tests pass on the original code", "verified",
                       [item("execution", r["name"], f"{r['category']}: {'PASS' if r['passed'] else 'FAIL'} — got {r['actual'][:60]!r}") for r in results[:8]], "Proof Mode")]
        if cerr:
            evidence.append(ev("The code does not compile", "verified", [item("compiler", "build", cerr["stderr"][:300])], "Proof Mode"))
        evidence.append(ev("The expected outputs were predicted by the AI, not computed", "ai", [item("note", "expectation", "A FAIL can mean the expectation is wrong — see the verdicts.")], "Proof Mode"))
        for v in verdicts:
            evidence.append(ev(f"{v.get('test')}: {v.get('cause')}", "ai", [item("explanation", v.get("test", ""), str(v.get("explanation", ""))[:300])], "Proof Mode"))
        if len(rounds) > 1:
            evidence.append(ev(f"Fixed code passes {passed}/{len(final['results'])} tests", "verified" if passed == len(final["results"]) else "uncertain",
                               [item("execution", "re-run", f"{passed}/{len(final['results'])} pass")], "Proof Mode"))
        bump("proof_runs")
        yield {"done": {"mode": mode, "rounds": rounds, "verdicts": verdicts, "summary": summary, "fixed_code": fixed, "diff": fixdiff, "disagreements": disagree,
                        "total": len(final["results"]), "passed": passed, "evidence": evidence}}
    return ndjson(gen)


# ═════════════════════ Review Tribunal ═════════════════════
REVIEWERS = {
    "correctness": "You are the CORRECTNESS reviewer. Look only for logic errors, wrong conditions, off-by-one mistakes, unhandled edge cases, wrong results and resource leaks. Ignore style, speed and security unless they change the behaviour.",
    "performance": "You are the PERFORMANCE reviewer. Look only for algorithmic complexity, wasted work, unnecessary allocation, slow I/O patterns and poor data-structure choices. Ignore style and security.",
    "security": "You are the SECURITY reviewer. Look only for injection, unsafe input handling, secrets, authentication/authorization gaps, unsafe deserialization and crypto misuse. Ignore style and speed.",
}
REVIEW_FORMAT = ("Reply with ONE JSON object: {\"verdict\": \"approve\"|\"changes_requested\"|\"reject\", \"summary\": string, \"findings\": [{\"severity\": \"info\"|\"low\"|\"medium\"|\"high\"|\"critical\", "
                 "\"line\": integer or null, \"title\": string, \"detail\": string, \"evidence_line\": string (copy the exact source line), \"suggestion\": string}]}. At most 6 findings. "
                 "Every finding must point at a real line of the numbered code. If you find nothing in your area, approve with an empty list.")
JUDGE_SYS = ("Three reviewers (correctness, performance, security) reviewed the same code. Compare their reviews. Reply with ONE JSON object: {\"consensus\": string, "
             "\"disagreements\": [{\"topic\": string, \"positions\": [{\"reviewer\": string, \"stance\": string}], \"advice\": string}], \"priority\": [string]}. A disagreement is a real conflict "
             "(different verdicts, or advice that pulls in opposite directions such as 'cache this' vs 'do not keep this in memory'). Do not invent conflicts.")
SEV = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _review(key, role, lang, code, context):
    txt, err = complete(key, REVIEWERS[role] + " " + REVIEW_FORMAT, f"Language: {lang}\nContext: {context or '(none)'}\n\nNumbered code:\n{numbered(code)}", max_tokens=2500, json_mode=True)
    if err:
        return {"role": role, "error": err}
    obj = extract_json(txt) or {}
    lines = code.split("\n")
    out = []
    for f in (obj.get("findings") or [])[:6]:
        if not isinstance(f, dict):
            continue
        ln = f.get("line") if isinstance(f.get("line"), int) else None
        ev_line = str(f.get("evidence_line", "")).strip()
        actual = lines[ln - 1].strip() if ln and 0 < ln <= len(lines) else ""
        verified = None if ln is None else bool(ev_line and actual and (ev_line in actual or actual in ev_line))
        out.append({"severity": f.get("severity") if f.get("severity") in SEV else "low", "line": ln, "title": str(f.get("title", ""))[:140], "detail": str(f.get("detail", ""))[:500],
                    "suggestion": str(f.get("suggestion", ""))[:400], "evidence_line": ev_line[:200], "actual_line": actual[:200], "location_verified": verified})
    return {"role": role, "verdict": obj.get("verdict") if obj.get("verdict") in ("approve", "changes_requested", "reject") else "changes_requested", "summary": str(obj.get("summary", ""))[:400], "findings": out}


@bp.route("/api/labs/tribunal", methods=["POST"])
def tribunal():
    d, code, lang = _code_body()
    context = str(d.get("context", ""))[:800]
    key = _key()
    _gate("ai", 4)

    def gen():
        yield {"step": "review", "msg": "Three reviewers are reading the code in parallel…"}
        with ThreadPoolExecutor(3) as ex:
            reviews = list(ex.map(lambda r: _review(key, r, lang, code, context), REVIEWERS))
        good = [r for r in reviews if "error" not in r]
        if not good:
            raise ApiError(reviews[0].get("error", "The reviewers could not respond."), 502)
        yield {"step": "compare", "msg": "Comparing their findings and looking for disagreements…"}
        # shared findings: different reviewers pointing at the same place
        shared, flat = [], [(r["role"], f) for r in good for f in r["findings"] if f["line"]]
        for i, (ra, fa) in enumerate(flat):
            grp = [(ra, fa)] + [(rb, fb) for rb, fb in flat[i + 1:] if rb != ra and abs(fb["line"] - fa["line"]) <= 2]
            roles = {x[0] for x in grp}
            if len(roles) > 1 and not any(set(s["roles"]) == roles and s["line"] == fa["line"] for s in shared):
                shared.append({"line": fa["line"], "roles": sorted(roles), "titles": [f"{a}: {b['title']}" for a, b in grp],
                               "severity_gap": max(SEV[b["severity"]] for _, b in grp) - min(SEV[b["severity"]] for _, b in grp)})
        det = []
        verdicts = {r["role"]: r["verdict"] for r in good}
        if "approve" in verdicts.values() and any(v != "approve" for v in verdicts.values()):
            det.append({"topic": "The reviewers do not agree on whether to approve", "positions": [{"reviewer": k, "stance": v.replace("_", " ")} for k, v in verdicts.items()], "advice": "Resolve the highest-severity findings first, then re-review.", "source": "verdicts"})
        for s in shared:
            if s["severity_gap"] >= 2:
                det.append({"topic": f"Severity differs around line {s['line']}", "positions": [{"reviewer": t.split(":")[0], "stance": t.split(": ", 1)[1]} for t in s["titles"]], "advice": "Treat the higher severity as the working assumption until verified.", "source": "severity"})
        consensus, ai_dis, priority = "", [], []
        jtxt, jerr = complete(key, JUDGE_SYS, json.dumps([{k: r[k] for k in ("role", "verdict", "summary", "findings")} for r in good])[:12000], max_tokens=1800, json_mode=True)
        if not jerr:
            jo = extract_json(jtxt) or {}
            consensus = str(jo.get("consensus", ""))[:500]
            ai_dis = [{**x, "source": "judge"} for x in (jo.get("disagreements") or []) if isinstance(x, dict) and x.get("topic")][:4]
            priority = [str(p)[:200] for p in (jo.get("priority") or [])[:6]]
        total = sum(len(r["findings"]) for r in good)
        vf = sum(1 for r in good for f in r["findings"] if f["location_verified"])
        evidence = [ev(f"{r['role'].title()} reviewer: {r['verdict'].replace('_', ' ')} ({len(r['findings'])} finding(s))", "ai", [item("summary", r["role"], r["summary"])], "Tribunal") for r in good]
        for r in good:
            for f in r["findings"][:3]:
                evidence.append(ev(f"[{r['role']}/{f['severity']}] {f['title']}", "static" if f["location_verified"] else "ai",
                                   [item("code_location", f"line {f['line']}", f["actual_line"] or "(no line)"), item("check", "location", "the quoted line really exists there" if f["location_verified"] else "the quoted line could NOT be matched — treat as unverified")], "Tribunal"))
        bump("tribunal_runs")
        yield {"done": {"reviews": reviews, "shared": shared, "disagreements": det + ai_dis, "consensus": consensus, "priority": priority, "verified_locations": vf, "total_findings": total, "evidence": evidence}}
    return ndjson(gen)


# ═════════════════════ Specification-to-Test ═════════════════════
SPEC_SYS = ("You turn written requirements into executable tests for the given program. Reply with ONE JSON object: {\"mode\": \"io\"|\"unit\", \"requirements\": [{\"id\": string, "
            "\"text\": string, \"testable\": boolean, \"reason\": string (why not testable), \"tests\": [{\"name\": string, \"input\": string, \"expected\": string, \"snippet\": string}]}]}. "
            "io mode: each test gives the exact stdin and the exact expected stdout. unit mode (Python or JavaScript only): each test has a snippet appended after the program, using assert, "
            "that fails if the requirement is violated. Write 1 to 3 tests per requirement and aim at the requirement's boundary (e.g. 'never negative' -> try to overdraw). "
            "Mark a requirement testable=false when it cannot be checked by running the program.")


def parse_requirements(text):
    reqs = []
    for ln in re.split(r"\n+", str(text)):
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip()
        if len(ln) >= 6:
            reqs.append(ln[:300])
    return reqs[:8]


@bp.route("/api/labs/spec", methods=["POST"])
def spec_to_test():
    d, code, lang = _code_body()
    reqs = parse_requirements(d.get("requirements", ""))
    if not reqs:
        raise ApiError("Write at least one requirement (one per line).")
    key = _key()
    _gate("ai")
    _need_runner(lang)
    _gate("run", 16)

    def gen():
        yield {"step": "tests", "msg": f"Turning {len(reqs)} requirement(s) into tests…"}
        listing = "\n".join(f"R{i}: {r}" for i, r in enumerate(reqs, 1))
        txt, err = complete(key, SPEC_SYS, f"Language: {lang}\n\nRequirements:\n{listing}\n\nProgram:\n{code}", max_tokens=4500, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        mode = "unit" if obj.get("mode") == "unit" and lang.lower() in ("python", "javascript") else "io"
        out, cerr = [], None
        for i, rq in enumerate((obj.get("requirements") or [])[:8], 1):
            if not isinstance(rq, dict):
                continue
            tests = [t for t in (rq.get("tests") or [])[:3] if isinstance(t, dict) and ((mode == "unit" and t.get("snippet")) or (mode == "io" and "input" in t and "expected" in t))]
            row = {"id": str(rq.get("id") or f"R{i}"), "text": str(rq.get("text") or (reqs[i - 1] if i <= len(reqs) else ""))[:300], "testable": bool(rq.get("testable", True)) and bool(tests), "reason": str(rq.get("reason", ""))[:300], "tests": []}
            if row["testable"]:
                yield {"step": "run", "msg": f"Running tests for {row['id']}…"}
                results, cerr = run_defined_tests(lang, code, mode, tests)
                if cerr:
                    break
                row["tests"] = results
                row["verdict"] = "satisfied" if all(r["passed"] for r in results) else "violated"
            else:
                row["verdict"] = "untestable"
                row["reason"] = row["reason"] or "No executable test could be written for this requirement."
            out.append(row)
        if cerr:
            raise ApiError("The program does not compile, so no requirement can be checked: " + cerr["stderr"][:300], 422)
        sat = sum(1 for r in out if r["verdict"] == "satisfied")
        vio = sum(1 for r in out if r["verdict"] == "violated")
        evidence = []
        for r in out:
            st = "verified" if r["verdict"] in ("satisfied", "violated") else "uncertain"
            evidence.append(ev(f"{r['id']} {r['verdict'].upper()}: {r['text']}", st,
                               [item("execution", t["name"], f"{'PASS' if t['passed'] else 'FAIL'} — {(t['actual'] or t['stderr'])[:100]!r}") for t in r["tests"]] or [item("note", r["id"], r["reason"])], "Spec→Test"))
        evidence.append(ev("The test inputs and expectations were written by the AI from your wording", "ai", [item("note", "tests", "Review the generated tests; a wrong test gives a wrong verdict.")], "Spec→Test"))
        bump("spec_runs")
        yield {"done": {"mode": mode, "requirements": out, "satisfied": sat, "violated": vio, "untestable": len(out) - sat - vio, "evidence": evidence}}
    return ndjson(gen)


# ═════════════════════ Merge conflict resolver ═════════════════════
def parse_conflicts(text):
    lines, segs, buf, i = text.split("\n"), [], [], 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            if buf:
                segs.append(("text", "\n".join(buf)))
                buf = []
            ours, base, theirs, state, j = [], [], [], "ours", i + 1
            while j < len(lines) and not lines[j].startswith(">>>>>>>"):
                if lines[j].startswith("|||||||") and state == "ours":
                    state = "base"
                elif lines[j].startswith("=======") and state in ("ours", "base"):
                    state = "theirs"
                else:
                    {"ours": ours, "base": base, "theirs": theirs}[state].append(lines[j])
                j += 1
            if j >= len(lines) or state != "theirs":
                raise ApiError("A conflict block is not closed properly (missing ======= or >>>>>>>).")
            segs.append(("conflict", {"ours": "\n".join(ours), "theirs": "\n".join(theirs), "base": "\n".join(base) if base else None,
                                      "ours_label": lines[i][7:].strip(), "theirs_label": lines[j][7:].strip()}))
            i = j + 1
        else:
            buf.append(lines[i])
            i += 1
    if buf:
        segs.append(("text", "\n".join(buf)))
    return segs


def assemble(segs, pick):
    """pick(index, conflict) -> replacement text."""
    out, n = [], 0
    for kind, v in segs:
        if kind == "text":
            out.append(v)
        else:
            out.append(pick(n, v))
            n += 1
    return "\n".join(out)


def build_conflict(ours, theirs):
    a, b = ours.split("\n"), theirs.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += a[i1:i2]
        else:
            out += ["<<<<<<< ours"] + a[i1:i2] + ["======="] + b[j1:j2] + [">>>>>>> theirs"]
    return "\n".join(out)


def syntax_check(lang, code):
    l = lang.lower()
    if l == "python":
        try:
            ast.parse(code)
            return {"checked": True, "ok": True, "message": "Python syntax is valid"}
        except SyntaxError as e:
            return {"checked": True, "ok": False, "message": f"Python syntax error at line {e.lineno}: {e.msg}"}
    if l == "json":
        try:
            json.loads(code)
            return {"checked": True, "ok": True, "message": "JSON is valid"}
        except ValueError as e:
            return {"checked": True, "ok": False, "message": f"JSON error: {e}"}
    if l in ("javascript", "typescript") and shutil.which("node") and l == "javascript":
        d = tempfile.mkdtemp(prefix="cpm_")
        try:
            p = os.path.join(d, "m.js")
            open(p, "w", encoding="utf-8").write(code)
            r = subprocess.run(["node", "--check", p], capture_output=True, timeout=8)
            return {"checked": True, "ok": r.returncode == 0, "message": "JavaScript syntax is valid" if r.returncode == 0 else r.stderr.decode("utf-8", "replace")[:300]}
        except (subprocess.SubprocessError, OSError):
            return {"checked": False, "ok": None, "message": "Could not run the syntax check"}
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return {"checked": False, "ok": None, "message": f"No syntax checker is available for {lang}"}


def pass_count(lang, code, tests):
    if not tests:
        return None
    n = 0
    for t in tests:
        r = _run(lang, code, str(t.get("input", "")))
        n += r["status"] == "ok" and norm(r["stdout"]) == norm(t.get("expected", ""))
    return n


MERGE_SYS = ("You resolve Git merge conflicts. You get numbered conflict hunks (ours, theirs and sometimes the base) with surrounding context. Reply with ONE JSON object: "
             "{\"explanation\": string (why the conflict happened, in plain words), \"hunks\": [{\"id\": integer, \"why\": string, \"strategy\": \"ours\"|\"theirs\"|\"both\"|\"custom\", "
             "\"resolution\": string (the exact text that replaces the whole conflict block, no markers)}]}. Keep the intent of both sides when they do not contradict each other.")


@bp.route("/api/labs/merge/build", methods=["POST"])
def merge_build():
    d = request.get_json(silent=True) or {}
    ours, theirs = str(d.get("ours", "")), str(d.get("theirs", ""))
    if not ours.strip() or not theirs.strip() or max(len(ours), len(theirs)) > MAX_CODE:
        raise ApiError("Paste both versions (each under 24,000 characters).")
    text = build_conflict(ours, theirs)
    return jsonify(content=text, conflicts=text.count("<<<<<<< ours"))


@bp.route("/api/labs/merge", methods=["POST"])
def merge():
    d, content, lang = _code_body()
    segs = parse_conflicts(content)
    nconf = sum(1 for k, _ in segs if k == "conflict")
    if not nconf:
        raise ApiError("No conflict markers (<<<<<<<, =======, >>>>>>>) were found.")
    if nconf > 12:
        raise ApiError("This tool handles up to 12 conflicts at a time.")
    tests = [t for t in (d.get("tests") or [])[:6] if isinstance(t, dict) and (str(t.get("input", "")).strip() or str(t.get("expected", "")).strip())]
    key = _key()
    _gate("ai")
    if tests:
        _need_runner(lang)
        _gate("run", len(tests) * 3)

    def gen():
        yield {"step": "parse", "msg": f"Found {nconf} conflict(s). Explaining and proposing a merge…"}
        hunks_txt, n = [], 0
        ctx_lines = [l for k, v in segs for l in (v.split("\n") if k == "text" else [None])]
        for idx, (kind, v) in enumerate(segs):
            if kind != "conflict":
                continue
            before = segs[idx - 1][1].split("\n")[-4:] if idx > 0 and segs[idx - 1][0] == "text" else []
            after = segs[idx + 1][1].split("\n")[:4] if idx + 1 < len(segs) and segs[idx + 1][0] == "text" else []
            hunks_txt.append(f"HUNK {n}\n[context before]\n" + "\n".join(before) + f"\n[OURS {v['ours_label']}]\n{v['ours']}\n[THEIRS {v['theirs_label']}]\n{v['theirs']}\n" + (f"[BASE]\n{v['base']}\n" if v["base"] is not None else "") + "[context after]\n" + "\n".join(after))
            n += 1
        txt, err = complete(key, MERGE_SYS, f"Language: {lang}\n\n" + "\n\n".join(hunks_txt), max_tokens=4000, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        by_id = {h.get("id"): h for h in (obj.get("hunks") or []) if isinstance(h, dict)}
        res_text, hunks = {}, []
        n = 0
        for kind, v in segs:
            if kind == "conflict":
                h = by_id.get(n) or {}
                resolution = h.get("resolution") if isinstance(h.get("resolution"), str) else None
                if resolution is None or re.search(r"^(<<<<<<<|=======|>>>>>>>)", resolution, re.M):
                    resolution, strategy, why = v["ours"], "ours", "The AI gave no usable resolution, so our side was kept — review this hunk."
                else:
                    strategy, why = h.get("strategy", "custom"), str(h.get("why", ""))[:300]
                res_text[n] = resolution
                hunks.append({"id": n, "ours": v["ours"][:1500], "theirs": v["theirs"][:1500], "base": (v["base"] or "")[:1500] if v["base"] is not None else None, "strategy": strategy, "why": why, "resolution": resolution[:3000]})
                n += 1
        merged = assemble(segs, lambda i, c: res_text[i])
        ours_all, theirs_all = assemble(segs, lambda i, c: c["ours"]), assemble(segs, lambda i, c: c["theirs"])
        yield {"step": "verify", "msg": "Checking syntax and running tests on ours, theirs and the merged result…"}
        syn = {k: syntax_check(lang, v) for k, v in (("merged", merged), ("ours", ours_all), ("theirs", theirs_all))}
        counts = {}
        if tests:
            for k, v in (("merged", merged), ("ours", ours_all), ("theirs", theirs_all)):
                counts[k] = pass_count(lang, v, tests) if syn[k]["ok"] is not False else 0
        leftover = bool(re.search(r"^(<<<<<<<|=======|>>>>>>>)", merged, re.M))
        reasons = []
        if leftover:
            rec, reasons = "reject", ["conflict markers remain in the merged text"]
        elif syn["merged"]["ok"] is False:
            rec, reasons = "reject", [syn["merged"]["message"]]
        elif tests and counts["merged"] == len(tests):
            rec, reasons = "accept", [f"the merged result passes all {len(tests)} test(s)" + (f" (ours {counts['ours']}/{len(tests)}, theirs {counts['theirs']}/{len(tests)})" if counts["ours"] is not None else "")]
        elif tests:
            rec, reasons = "review", [f"the merged result passes only {counts['merged']}/{len(tests)} tests (ours {counts['ours']}, theirs {counts['theirs']})"]
        else:
            rec, reasons = "review", ["no tests were provided, so behaviour was not verified" + (" — syntax looks valid" if syn["merged"]["ok"] else "")]
        evidence = [ev(f"Syntax check of the merged file: {syn['merged']['message']}", "verified" if syn["merged"]["checked"] else "uncertain", [item("compiler", "merged", syn["merged"]["message"])], "Merge")]
        if tests:
            evidence.append(ev(f"Tests: merged {counts['merged']}/{len(tests)}, ours {counts['ours']}/{len(tests)}, theirs {counts['theirs']}/{len(tests)}", "verified", [item("execution", "tests", "real runs of all three versions")], "Merge"))
        for h in hunks:
            evidence.append(ev(f"Conflict {h['id'] + 1}: {h['strategy']} — {h['why']}", "ai", [item("resolution", f"hunk {h['id'] + 1}", h["resolution"][:200])], "Merge"))
        bump("merge_runs")
        yield {"done": {"explanation": str(obj.get("explanation", ""))[:800], "hunks": hunks, "merged": merged, "syntax": syn, "tests": {"count": len(tests), **counts} if tests else None,
                        "recommendation": rec, "reasons": reasons, "evidence": evidence}}
    return ndjson(gen)


# ═════════════════════ Security Attack Simulator ═════════════════════
def R(rid, name, sev, cwe, pat, why, fix, langs=None, demo=None, skip=None):
    return {"id": rid, "name": name, "severity": sev, "cwe": cwe, "re": re.compile(pat.replace("(?i)", ""), re.I), "why": why, "fix": fix, "langs": langs, "demo": demo, "skip": re.compile(skip) if skip else None}


PY, JS, JAVA, PHP, C_ = (".py",), (".js", ".jsx", ".ts", ".tsx", ".mjs"), (".java", ".kt"), (".php",), (".c", ".cpp", ".h", ".cc")
RULES = [
    R("sqli", "SQL built from text (injection)", "high", "CWE-89", r"(?i)\.(?:execute|executemany|raw|query)\w*\(\s*(?:f[\"'][^\"']*\{|[\"'][^\"']*[\"']\s*(?:%|\+)|[\"'][^\"']*[\"']\.format\()|(?i)\b(?:select|insert|update|delete)\b[^\n]*(?:\{[^}]+\}|[\"']\s*\+\s*\w)",
      "User input concatenated into SQL can change the meaning of the query.", "Use parameterized queries: cursor.execute('... WHERE id = ?', (value,)).", None, "sqli", r"^\s*(#|//)"),
    R("cmdi", "Shell command built from input", "high", "CWE-78", r"shell\s*=\s*True|\bos\.(?:system|popen)\(\s*(?:f[\"']|[^\"')\s]|[\"'][^\"']*[\"']\s*[+%])|child_process\.exec\(\s*(?:`|[^\"')\s]|[\"'][^\"']*[\"']\s*\+)|Runtime\.getRuntime\(\)\.exec\(|\b(?:shell_exec|passthru|system)\(\s*\$",
      "A shell interprets ; & | and $() in the string, so input can run extra commands.", "Pass an argument list without a shell (subprocess.run([...])) and validate input.", None, "cmdi", r"^\s*(#|//)"),
    R("eval", "eval / exec on dynamic input", "high", "CWE-95", r"(?<![\w.])(?:eval|exec)\(\s*(?![\"'][^\"']*[\"']\s*\))|new\s+Function\(", "Evaluating text runs arbitrary code with the program's rights.", "Parse data with json.loads / ast.literal_eval; never evaluate user text.", None, "eval", r"^\s*(#|//)"),
    R("deser", "Unsafe deserialization", "high", "CWE-502", r"\bpickle\.loads?\(|\bmarshal\.loads?\(|yaml\.load\((?![^)]*SafeLoader)|ObjectInputStream|unserialize\(", "Loading untrusted pickles/objects can execute code while decoding.", "Use json for untrusted data, or yaml.safe_load.", None, "deser", r"^\s*(#|//)"),
    R("secret", "Hard-coded secret", "high", "CWE-798", r"(?i)(?:api[_-]?key|secret|token|passwd|password|pwd)\w*\s*[:=]\s*[\"'](?![^\"']*(?:your_|example|changeme|xxxx|<|placeholder|\*\*\*))[^\"'\s]{8,}[\"']|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9]{20,}|\bgsk_[A-Za-z0-9]{20,}",
      "Secrets in source code leak through version control and logs.", "Load secrets from environment variables or a secret manager and rotate the exposed one.", None, None, r"^\s*(#|//)"),
    R("xss", "Unescaped HTML output (XSS)", "high", "CWE-79", r"\.innerHTML\s*=\s*(?![\"'`][^\"'`$]*[\"'`]\s*;?\s*$)|document\.write\(|dangerouslySetInnerHTML|render_template_string\(\s*f?[\"'][^\"']*(?:\{|%)|\|\s*safe\b|\bMarkup\(", "Inserting input as HTML lets an attacker run script in other users' browsers.", "Escape output (textContent, template auto-escaping) and sanitize rich text.", None, "xss", r"^\s*(#|//)"),
    R("path", "File path built from input (traversal)", "medium", "CWE-22", r"(?:open|send_file|send_from_directory|readFile(?:Sync)?|FileInputStream)\(\s*[^\"')]*(?:request\.|req\.(?:query|params|body)|input\(|sys\.argv|\$_(?:GET|POST|REQUEST))", "../ sequences can escape the intended directory.", "Resolve the path and check it stays inside the allowed base directory (commonpath).", None, "path", r"^\s*(#|//)"),
    R("hash", "Weak hash for secrets", "medium", "CWE-327", r"hashlib\.(?:md5|sha1)\(|MessageDigest\.getInstance\(\s*[\"'](?:MD5|SHA-?1)[\"']|createHash\(\s*[\"'](?:md5|sha1)[\"']|\bmd5\(", "MD5/SHA-1 are fast and broken; passwords hashed with them are quickly recovered.", "Use bcrypt, scrypt or argon2 with a per-user salt.", None, "hash", r"^\s*(#|//)"),
    R("random", "Predictable random for tokens", "medium", "CWE-338", r"(?i)(?:random\.(?:random|randint|choice|randrange)|Math\.random)\(.*(?:token|secret|password|session|key|otp|nonce)|(?:token|secret|password|session|key|otp|nonce).*(?:random\.(?:random|randint|choice|randrange)|Math\.random)\(", "Seeded pseudo-random values can be predicted.", "Use secrets.token_urlsafe() / crypto.randomBytes().", None, "random", r"^\s*(#|//)"),
    R("debug", "Debug mode enabled", "medium", "CWE-489", r"\bdebug\s*=\s*True\b|app\.debug\s*=\s*True|DEBUG\s*=\s*True", "Debug pages expose source code and an interactive console.", "Disable debug in production via configuration.", PY, None, r"^\s*(#|//)"),
    R("tls", "TLS verification disabled", "medium", "CWE-295", r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)", "Certificates are not checked, so a man-in-the-middle can read and alter traffic.", "Keep certificate verification on; fix the trust store instead.", None, None, r"^\s*(#|//)"),
    R("jwt", "JWT signature not verified", "high", "CWE-347", r"verify\s*=\s*False.*jwt|jwt\.decode\([^)]*verify_signature[\"']?\s*:\s*False|algorithms\s*=\s*\[\s*[\"']none[\"']", "Unsigned tokens can be forged.", "Always verify the signature with a fixed algorithm list.", None, None, r"^\s*(#|//)"),
    R("cors", "CORS allows every origin", "low", "CWE-942", r"Access-Control-Allow-Origin[\"']?\s*[:,=]\s*[\"']\*[\"']|origin\s*:\s*[\"']\*[\"']|CORS\(\s*\w+\s*\)", "Any website can call this API from a visitor's browser.", "Allow only the origins you control.", None, None, r"^\s*(#|//)"),
    R("cookie", "Insecure session cookie settings", "low", "CWE-614", r"SESSION_COOKIE_(?:SECURE|HTTPONLY)\s*=\s*False|httpOnly\s*:\s*false|secure\s*:\s*false", "Cookies can be stolen by script or sent over plain HTTP.", "Set Secure, HttpOnly and SameSite.", None, None, r"^\s*(#|//)"),
    R("plain", "Plain-text password comparison", "low", "CWE-256", r"(?i)\bpassword\w*\s*==\s*(?!None|null|\"\"|'')", "Comparing stored plain-text passwords means they are stored unhashed.", "Store salted hashes and compare with a constant-time check.", None, None, r"^\s*(#|//)"),
    R("cbuf", "Unsafe C string function", "high", "CWE-120", r"\b(?:gets|strcpy|strcat|sprintf|vsprintf)\s*\(", "These functions do not check buffer size (buffer overflow).", "Use fgets, strncpy/snprintf or safer string types.", C_, None, r"^\s*(#|//)"),
]
DEMOS = {
    "sqli": ("SQL injection", "Same data, same table: the unsafe query leaks every row; the parameterized one returns nothing.",
             'import sqlite3\ndb = sqlite3.connect(":memory:")\ndb.execute("CREATE TABLE users(name TEXT, secret TEXT)")\ndb.executemany("INSERT INTO users VALUES(?,?)", [("alice","a-secret"),("bob","b-secret")])\npayload = "x\' OR \'1\'=\'1"\nunsafe = f"SELECT name, secret FROM users WHERE name = \'{payload}\'"\nprint("VULNERABLE query:", unsafe)\nprint("  rows leaked:", db.execute(unsafe).fetchall())\nprint("SAFE (parameterized):")\nprint("  rows returned:", db.execute("SELECT name, secret FROM users WHERE name = ?", (payload,)).fetchall())\n'),
    "cmdi": ("Command injection", "Shows how one string becomes several commands. Nothing is executed.",
             'import shlex\nfilename = "report.txt; echo INJECTED"\ncmd = f"cat {filename}"\nparts = [p.strip() for p in cmd.split(";") if p.strip()]\nprint("Command string:", cmd)\nprint("A shell would run", len(parts), "commands:", parts)\nprint("Safe: argument list ->", ["cat", filename], "(one program, one literal file name)")\nprint("Safe quoting:", shlex.quote(filename))\n'),
    "path": ("Path traversal", "A relative path escapes the upload folder; a commonpath check catches it.",
             'import posixpath as pp\nbase = "/srv/app/uploads"\nuser = "../../etc/passwd"\njoined = pp.normpath(pp.join(base, user))\nprint("Requested:", user)\nprint("Resolves to:", joined)\nprint("Still inside the upload folder?", joined.startswith(base + "/"))\nprint("Safe check passes?", pp.commonpath([base, joined]) == base)\n'),
    "xss": ("Cross-site scripting", "Unescaped input becomes live script; escaping makes it inert text.",
            'import html\npayload = "<script>alert(1)</script>"\nprint("Unsafe page :", "<p>Hello " + payload + "</p>")\nprint("Escaped page:", "<p>Hello " + html.escape(payload) + "</p>")\n'),
    "deser": ("Unsafe deserialization", "Loading the data runs a harmless print — proof that code runs during decoding.",
              'import pickle\nclass Demo:\n    def __reduce__(self):\n        return (print, ("DEMO: this code ran just by loading the data",))\nblob = pickle.dumps(Demo())\nprint("Loading an untrusted pickle...")\npickle.loads(blob)\nprint("Safe: json.loads can only produce plain data.")\n'),
    "hash": ("Weak password hash", "A 4-digit PIN stored as MD5 is recovered instantly.",
             'import hashlib, time\npin = "4821"\nh = hashlib.md5(pin.encode()).hexdigest()\nt = time.time()\nfor n in range(10000):\n    if hashlib.md5(f"{n:04d}".encode()).hexdigest() == h:\n        print("Recovered PIN", f"{n:04d}", "from its MD5 in", round((time.time() - t) * 1000, 1), "ms")\n        break\nprint("Use bcrypt/scrypt/argon2 with a salt: they are deliberately slow.")\n'),
    "random": ("Predictable tokens", "Two tokens from the same seed are identical.",
               'import random\nrandom.seed(1234); a = [random.randint(0, 9) for _ in range(8)]\nrandom.seed(1234); b = [random.randint(0, 9) for _ in range(8)]\nprint("token A:", a)\nprint("token B:", b)\nprint("Identical:", a == b, "- predictable once the seed is known. Use the secrets module.")\n'),
    "eval": ("eval() on input", "Input chosen by the caller runs as code (this one only computes a number).",
             'user_input = "__import__(\'math\').pi * 2"\nprint("eval() ran caller-chosen code and returned:", eval(user_input))\nprint("It could just as easily have imported os. Use ast.literal_eval for data.")\n'),
}


def scan_files(files):
    out, seen = [], set()
    for path, text in files.items():
        ext = os.path.splitext(path)[1].lower()
        for n, ln in enumerate(text.split("\n"), 1):
            if len(ln) > 400:
                continue
            for r in RULES:
                if r["langs"] and ext not in r["langs"]:
                    continue
                if r["skip"] and r["skip"].search(ln):
                    continue
                if r["re"].search(ln) and (r["id"], path, n) not in seen:
                    seen.add((r["id"], path, n))
                    out.append({"rule": r["id"], "name": r["name"], "severity": r["severity"], "cwe": r["cwe"], "path": path, "line": n, "snippet": ln.strip()[:200], "why": r["why"], "fix": r["fix"], "demo": r["demo"]})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["path"], f["line"]))[:100]


@bp.route("/api/labs/attack/scan", methods=["POST"])
def attack_scan():
    d = request.get_json(silent=True) or {}
    files = d.get("files")
    if d.get("project_id"):
        import projects
        projects.project_row(str(d["project_id"]))
        files, _ = projects.snap_files(str(d["project_id"]))
    if not files:
        code = d.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ApiError("Put some code in the editor first.")
        files = {"editor" + {"python": ".py", "javascript": ".js", "php": ".php", "java": ".java", "c": ".c", "c++": ".cpp", "typescript": ".ts"}.get(str(d.get("language", "")).lower(), ".txt"): code}
    if not isinstance(files, dict) or sum(len(str(v)) for v in files.values()) > 700_000:
        raise ApiError("Too much code to scan at once.")
    found = scan_files({str(k): str(v) for k, v in files.items()})
    by = {}
    for f in found:
        by.setdefault(f["rule"], []).append(f)
    evidence = [ev(f"{f['name']} — {f['path']}:{f['line']} ({f['cwe']})", "static", [item("code_location", f"{f['path']}:{f['line']}", f["snippet"]), item("rule", f["cwe"], f["why"])], "Attack Simulator") for f in found[:10]]
    evidence.append(ev("Rule-based scanning finds known patterns only", "uncertain", [item("note", "limits", "A clean result does not prove the code is safe; a hit may be a false positive. Verify each finding.")], "Attack Simulator"))
    bump("attack_scans")
    return jsonify(findings=found, counts={k: len(v) for k, v in by.items()}, demos={k: DEMOS[k][0] for k in by if k in DEMOS}, evidence=evidence)


@bp.route("/api/labs/attack/demo", methods=["POST"])
def attack_demo():
    k = (request.get_json(silent=True) or {}).get("demo")
    if k not in DEMOS:
        raise ApiError("Unknown demonstration.")
    _gate("run")
    if not sandbox.backend() or "python" not in sandbox.languages():
        raise ApiError("Demonstrations run Python in the sandbox, which is not enabled on this server.", 503)
    title, expl, code = DEMOS[k]
    r = _run("python", code)
    bump("attack_demos")
    return jsonify(title=title, explanation=expl, code=code, status=r["status"], output=r["stdout"], stderr=r["stderr"], note="Generic, harmless demonstration of this class of bug on built-in sample data — it does not touch your code.",
                   evidence=[ev(f"Demonstration of {title}", "verified", [item("execution", k, r["stdout"][:300])], "Attack Simulator")])


SQL_ERR = re.compile(r"(?i)(sqlite3?\.\w*Error|SQL syntax|mysql_fetch|ORA-\d{5}|psycopg2|PG::|unterminated quoted string|SQLSTATE|near \"'\": syntax error|OperationalError|SequelizeDatabaseError)")
TRACE = re.compile(r"(?i)(Traceback \(most recent call last\)|Werkzeug Debugger|at [\w.$]+\([\w.]+\.java:\d+\)|Fatal error:.*on line \d+|stack trace)")
PATHERR = re.compile(r"(?i)(FileNotFoundError|No such file or directory|ENOENT|java\.io\.FileNotFoundException|failed to open stream)")


def loopback_target(url):
    u = urlparse(url if "://" in url else "http://" + url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ApiError("Enter a URL like http://127.0.0.1:5000/")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ApiError("That host name could not be resolved.")
    ips = sorted({i[4][0].split("%")[0] for i in infos})
    if not ips or any(not ipaddress.ip_address(ip).is_loopback for ip in ips):
        raise ApiError("For safety only localhost targets (127.0.0.1, ::1, localhost) can be tested. Never test systems you do not own.", 403)
    return u, ips[0]


def _probe_get(u, ip, path_query, headers=None):
    host = f"[{ip}]" if ":" in ip else ip
    port = u.port or (443 if u.scheme == "https" else 80)
    h = {"Host": u.netloc, "User-Agent": "CODEPHANTOM-local-probe/1.0"}
    h.update(headers or {})
    t0 = time.time()
    r = requests.get(f"{u.scheme}://{host}:{port}{path_query}", headers=h, timeout=4, allow_redirects=False, verify=False, stream=True)
    body = r.raw.read(200_000, decode_content=True).decode("utf-8", "replace")
    r.close()
    return r, body, int((time.time() - t0) * 1000)


@bp.route("/api/labs/attack/probe", methods=["POST"])
def attack_probe():
    if os.getenv("APP_ENV", "development").lower() == "production" and os.getenv("ALLOW_LOCAL_PROBE") != "1":
        raise ApiError("Local probing is disabled on production servers (it would target the server itself).", 403)
    d = request.get_json(silent=True) or {}
    if d.get("authorized") is not True:
        raise ApiError("Confirm that you own this application and are allowed to test it.", 403)
    u, ip = loopback_target(str(d.get("url", ""))[:300])
    _gate("run", 4)
    import urllib3
    urllib3.disable_warnings()
    base_pq = (u.path or "/") + (("?" + u.query) if u.query else "")
    params = [p for p in str(d.get("param", "")).replace(" ", "").split(",") if p][:3] or [k for k, _ in parse_qsl(u.query)][:3]
    reqs, findings, passed = [], [], []

    def fetch(label, pq, headers=None):
        try:
            r, body, ms = _probe_get(u, ip, pq, headers)
        except requests.RequestException as e:
            raise ApiError(f"Could not connect to {u.netloc}: {type(e).__name__}. Is the app running?", 502)
        reqs.append({"label": label, "path": pq[:160], "status": r.status_code, "ms": ms})
        return r, body

    def add(rid, title, sev, detail, fix, evid):
        findings.append({"id": rid, "title": title, "severity": sev, "detail": detail, "remediation": fix, "evidence": evid})

    base, base_body = fetch("baseline", base_pq)
    h = {k.lower(): v for k, v in base.headers.items()}
    for hdr, why, fix in (("x-content-type-options", "Browsers may sniff content types.", "Send X-Content-Type-Options: nosniff."), ("content-security-policy", "No policy limits where scripts can load from.", "Add a Content-Security-Policy."),
                          ("referrer-policy", "Full URLs may leak in the Referer header.", "Send Referrer-Policy: no-referrer or strict-origin-when-cross-origin.")):
        if hdr in h:
            passed.append(hdr)
        else:
            add("hdr-" + hdr, f"Missing header {hdr}", "low", why, fix, [item("http", "GET " + base_pq[:80], f"response has no {hdr} header")])
    if "x-frame-options" not in h and "frame-ancestors" not in h.get("content-security-policy", ""):
        add("hdr-frame", "Page can be framed (clickjacking)", "low", "No X-Frame-Options or frame-ancestors.", "Send X-Frame-Options: DENY or CSP frame-ancestors 'none'.", [item("http", "GET " + base_pq[:80], "no framing protection")])
    if "server" in h and re.search(r"\d", h["server"]):
        add("hdr-server", "Server version disclosed", "low", "The Server header reveals software and version.", "Hide or generalize the Server header.", [item("http", "response header", f"Server: {h['server']}")])
    for c in base.raw.headers.getlist("Set-Cookie") if hasattr(base.raw.headers, "getlist") else []:
        miss = [f for f, k in (("HttpOnly", "httponly"), ("SameSite", "samesite")) if k not in c.lower()]
        if miss:
            add("cookie-flags", "Cookie without " + " / ".join(miss), "medium", "Cookies lacking these flags are easier to steal or send cross-site.", "Set HttpOnly and SameSite (and Secure on HTTPS).", [item("http", "Set-Cookie", c.split(";")[0][:30] + "… " + c[c.find(";"):][:90])])
    if TRACE.search(base_body):
        add("debug-page", "Error trace / debugger visible", "high", "The page shows a stack trace or debugger.", "Turn debug mode off and show a generic error page.", [item("http", "GET " + base_pq[:80], TRACE.search(base_body).group(0))])
    r404, b404 = fetch("404 probe", f"/cpx-not-found-{uuid.uuid4().hex[:6]}")
    if TRACE.search(b404) or PATHERR.search(b404):
        add("verbose-404", "Verbose error page", "medium", "Unknown URLs return internal details.", "Return a generic 404/500 page.", [item("http", "GET /cpx-not-found-…", (TRACE.search(b404) or PATHERR.search(b404)).group(0))])
    rc, _ = fetch("CORS probe", base_pq, {"Origin": "http://evil.example"})
    acao = rc.headers.get("access-control-allow-origin", "")
    if acao == "*" or acao == "http://evil.example":
        add("cors", "CORS accepts an untrusted origin", "medium" if acao != "*" else "low", f"Access-Control-Allow-Origin: {acao}", "Allow only trusted origins.", [item("http", "Origin: http://evil.example", f"Access-Control-Allow-Origin: {acao}")])
    else:
        passed.append("cors")
    for p in params:
        def with_param(v):
            q = dict(parse_qsl(u.query))
            q[p] = v
            return (u.path or "/") + "?" + urlencode(q)
        marker = f"cpx{uuid.uuid4().hex[:5]}"
        r1, b1 = fetch(f"XSS probe ({p})", with_param(f'"><i id={marker}>'))
        if f"<i id={marker}>" in b1:
            add("xss-" + p, f"Parameter '{p}' is reflected without escaping", "high", "A harmless marker tag came back as live HTML, so script injection is possible.", "HTML-escape output (template auto-escaping) and validate input.", [item("http", f"GET ?{p}=\"><i id=…>", "the tag appears unescaped in the response")])
        else:
            passed.append(f"xss:{p}")
        r2, b2 = fetch(f"SQL probe ({p})", with_param("'"))
        if SQL_ERR.search(b2) and not SQL_ERR.search(base_body):
            add("sqli-" + p, f"Parameter '{p}' triggers a database error", "high", "A single quote produced a database error message: the value is probably concatenated into SQL.", "Use parameterized queries and hide error details.", [item("http", f"GET ?{p}='", SQL_ERR.search(b2).group(0))])
        r3, b3 = fetch(f"Traversal probe ({p})", with_param("../../cpx-traversal-test"))
        if PATHERR.search(b3) and not PATHERR.search(base_body):
            add("path-" + p, f"Parameter '{p}' reaches the file system", "medium", "A relative path produced a file-system error.", "Resolve and confine paths to an allowed folder.", [item("http", f"GET ?{p}=../../…", PATHERR.search(b3).group(0))])
    evidence = [ev(f["title"], "verified", f["evidence"], "Attack Simulator") for f in findings]
    evidence.append(ev(f"{len(reqs)} harmless GET request(s) sent to {u.netloc}; {len(passed)} check(s) passed", "verified", [item("http", r["label"], f"{r['path']} → {r['status']} ({r['ms']} ms)") for r in reqs[:8]], "Attack Simulator"))
    bump("attack_probes")
    return jsonify(target=u.netloc, requests=reqs, findings=findings, passed=passed, evidence=evidence,
                   note="Only harmless GET probes against localhost. A clean result does not prove the application is secure.")
