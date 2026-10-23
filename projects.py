"""Projects: upload a ZIP / import a GitHub repo / paste files, keep versions (snapshots), record test inputs, and run the
project-level features: X-Ray, Time Machine, Mission Mode, Change Impact and project Memory."""
import difflib
import io
import json
import os
import posixpath
import re
import time
import uuid
import zipfile

import requests
from flask import Blueprint, jsonify, request, session

import accounts
import sandbox
import xray
from groq_client import complete, extract_json, open_stream
from streamutil import ApiError, ev, item, ndjson

bp = Blueprint("projects", __name__)
bp.before_request(accounts._csrf)
allowed = bump = None          # injected by app.py

MAX_FILES, MAX_TOTAL, MAX_FILE, MAX_PROJECTS, MAX_SNAPSHOTS = 250, 600_000, 120_000, 10, 40
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", ".idea", ".vscode", "target", ".next", "vendor", ".mypy_cache", ".pytest_cache"}
NAMED = {"Dockerfile", "Makefile", "LICENSE", "README", ".gitignore"}
MAX_ZIP = 25_000_000


def init_db():
    with accounts.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT, created REAL, updated REAL,
            entry TEXT DEFAULT '', tests_json TEXT DEFAULT '[]', memory_json TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, label TEXT, ts REAL, files_json TEXT);
        CREATE INDEX IF NOT EXISTS snap_proj ON snapshots(project_id);
        """)


def owner():
    uid = session.get("uid")
    if uid:
        return f"u{uid}"
    if "anon" not in session:
        session["anon"] = uuid.uuid4().hex
        session.permanent = True
    return "a" + session["anon"]


def _key():
    k = os.getenv("GROQ_API_KEY")
    if not k:
        raise ApiError("Server has no GROQ_API_KEY.", 503)
    return k


def _gate(kind="ai", cost=1):
    msg = allowed(kind, cost)
    if msg:
        raise ApiError(msg, 429)


# ───────────────────────── storage ─────────────────────────
def project_row(pid):
    row = accounts.db().execute("SELECT * FROM projects WHERE id=? AND owner=?", (pid, owner())).fetchone()
    if not row:
        raise ApiError("Project not found.", 404)
    return row


def snap_list(pid):
    return accounts.db().execute("SELECT id,label,ts,length(files_json) AS size FROM snapshots WHERE project_id=? ORDER BY id", (pid,)).fetchall()


def snap_files(pid, sid=None):
    c = accounts.db()
    row = (c.execute("SELECT id,files_json FROM snapshots WHERE project_id=? AND id=?", (pid, sid)).fetchone() if sid
           else c.execute("SELECT id,files_json FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone())
    if not row:
        raise ApiError("Version not found.", 404)
    return json.loads(row["files_json"]), row["id"]


def add_snapshot(pid, label, files):
    c = accounts.db()
    cur = c.execute("INSERT INTO snapshots(project_id,label,ts,files_json) VALUES(?,?,?,?)", (pid, label[:80], time.time(), json.dumps(files)))
    c.execute("UPDATE projects SET updated=? WHERE id=?", (time.time(), pid))
    rows = c.execute("SELECT id FROM snapshots WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    for r in rows[1:max(1, len(rows) - MAX_SNAPSHOTS + 1)]:          # keep the first (original) version, drop the oldest extras
        c.execute("DELETE FROM snapshots WHERE id=?", (r["id"],))
    c.commit()
    return cur.lastrowid


def clean_files(files):
    if not isinstance(files, dict) or not files:
        raise ApiError("No files were provided.")
    out, total = {}, 0
    for k, v in files.items():
        try:
            p = sandbox.safe_rel(k)
        except sandbox.SandboxError as e:
            raise ApiError(str(e))
        if not isinstance(v, str) or "\0" in v:
            raise ApiError(f"{p} is not a text file.")
        if len(v) > MAX_FILE:
            raise ApiError(f"{p} is larger than {MAX_FILE // 1000} KB.")
        total += len(v)
        out[p] = v
    if len(out) > MAX_FILES or total > MAX_TOTAL:
        raise ApiError(f"A project can hold up to {MAX_FILES} files / {MAX_TOTAL // 1000} KB of text.")
    return out


def files_from_zip(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ApiError("That is not a valid ZIP file.")
    infos = [i for i in zf.infolist() if not i.is_dir()][:6000]
    names = [i.filename.replace("\\", "/") for i in infos]
    tops = {n.split("/")[0] for n in names if "/" in n}
    prefix = (tops.pop() + "/") if names and all("/" in n for n in names) and len(tops) == 1 else ""
    infos.sort(key=lambda i: (0 if xray.language_of(i.filename) in xray.CODE else 1, i.filename))
    out, skipped, total = {}, 0, 0
    for info in infos:
        n = info.filename.replace("\\", "/")
        if prefix and n.startswith(prefix):
            n = n[len(prefix):]
        parts = n.split("/")
        base = parts[-1]
        if (not n or n.startswith("/") or ".." in parts or any(p in SKIP_DIRS for p in parts[:-1])
                or (posixpath.splitext(base)[1].lower() not in xray.EXT_LANG and base not in NAMED) or info.file_size > MAX_FILE):
            skipped += 1
            continue
        with zf.open(info) as f:
            raw = f.read(MAX_FILE + 1)                    # capped read: a lying header cannot blow up memory
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        if len(raw) > MAX_FILE or "\0" in text or len(out) >= MAX_FILES or total + len(text) > MAX_TOTAL:
            skipped += 1
            continue
        out[n] = text
        total += len(text)
    if not out:
        raise ApiError("No readable source files were found in that archive.")
    return out, skipped


def files_from_github(ref):
    m = re.match(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?/?$", ref.strip())
    if not m:
        raise ApiError("Use a GitHub address like https://github.com/owner/repo or owner/repo.")
    owner_, repo = m.groups()
    try:
        r = requests.get(f"https://api.github.com/repos/{owner_}/{repo}/zipball", stream=True, timeout=25,
                         headers={"User-Agent": "codephantom-ai", "Accept": "application/vnd.github+json", **({"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]} if os.getenv("GITHUB_TOKEN") else {})})
    except requests.RequestException:
        raise ApiError("Could not reach GitHub.", 502)
    if r.status_code == 404:
        raise ApiError("Repository not found (it may be private — only public repositories can be imported).", 404)
    if r.status_code in (403, 429):
        raise ApiError("GitHub is rate-limiting this server. Try again later, or upload a ZIP instead.", 429)
    if r.status_code != 200:
        raise ApiError(f"GitHub returned an error ({r.status_code}).", 502)
    buf = io.BytesIO()
    for chunk in r.iter_content(65536):
        buf.write(chunk)
        if buf.tell() > MAX_ZIP:
            raise ApiError("That repository is too large to import (limit 25 MB). Upload a smaller ZIP.")
    files, skipped = files_from_zip(buf.getvalue())
    return files, skipped, repo


def norm(s):
    return "\n".join(l.rstrip() for l in str(s).replace("\r\n", "\n").split("\n")).strip()


def line_stats(a, b):
    sm = difflib.SequenceMatcher(None, a.split("\n"), b.split("\n"), autojunk=False)
    adds = dels = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            dels += i2 - i1
        if tag in ("replace", "insert"):
            adds += j2 - j1
    return adds, dels


def udiff(path, a, b):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{path}", f"b/{path}", n=2))[:30000]


def changed_lines(old, new):
    """Line numbers (1-based) in `new` that were added or changed relative to `old`."""
    out = set()
    sm = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            out |= set(range(j1 + 1, j2 + 1))
        elif tag == "delete":
            out.add(j1 + 1)
    return out


def run_project_tests(files, entry, tests, limit=8):
    if not entry or entry not in files:
        raise ApiError("Pick an entry file for the project first (Settings).")
    lang = xray.language_of(entry)
    if lang not in xray.CODE:
        raise ApiError("The entry file must be source code.")
    results, passed = [], 0
    for i, t in enumerate(tests[:limit], 1):
        r = sandbox.execute_files(lang, files, entry, str(t.get("stdin", "")))
        if r["status"] == "compile_error":
            return {"compile_error": r, "passed": 0, "total": len(tests[:limit]), "results": []}
        ok = r["status"] == "ok" and norm(r["stdout"]) == norm(t.get("expected", ""))
        passed += ok
        results.append({"index": i, "name": t.get("name") or f"Test {i}", "passed": ok, "status": r["status"], "stdin": str(t.get("stdin", ""))[:500],
                        "expected": str(t.get("expected", ""))[:2000], "actual": r["stdout"][:2000], "stderr": r["stderr"][:1500], "time_ms": r.get("time_ms")})
    return {"passed": passed, "total": len(results), "results": results}


_cache = {}


def get_index(pid, sid=None):
    files, real = snap_files(pid, sid)
    key = (pid, real)
    if key not in _cache:
        if len(_cache) > 48:
            _cache.pop(next(iter(_cache)))
        _cache[key] = xray.build_index(files)
    return files, _cache[key], real


def memory_text(row):
    try:
        entries = json.loads(row["memory_json"] or "[]")
    except ValueError:
        entries = []
    ok = [e for e in entries if e.get("approved")]
    return "\n".join(f"- [{e['kind']}] {e['text']}" for e in ok)[:1500]


def _tests(row):
    try:
        return json.loads(row["tests_json"] or "[]")
    except ValueError:
        return []


# ───────────────────────── project CRUD ─────────────────────────
@bp.route("/api/projects")
def list_projects():
    rows = accounts.db().execute("SELECT * FROM projects WHERE owner=? ORDER BY updated DESC", (owner(),)).fetchall()
    out = []
    for r in rows:
        snaps = snap_list(r["id"])
        files, _ = snap_files(r["id"])
        out.append({"id": r["id"], "name": r["name"], "files": len(files), "snapshots": len(snaps), "updated": r["updated"]})
    return jsonify(projects=out)


@bp.route("/api/projects", methods=["POST"])
def create_project():
    c = accounts.db()
    if c.execute("SELECT COUNT(*) FROM projects WHERE owner=?", (owner(),)).fetchone()[0] >= MAX_PROJECTS:
        raise ApiError(f"You can keep up to {MAX_PROJECTS} projects. Delete one first.")
    skipped, name = 0, ""
    up = request.files.get("zip")
    if up:
        files, skipped = files_from_zip(up.read(MAX_ZIP + 1))
        name = posixpath.splitext(up.filename or "project")[0]
    else:
        d = request.get_json(silent=True) or {}
        if d.get("github"):
            _gate("run")
            files, skipped, name = files_from_github(str(d["github"]))
        else:
            files, name = clean_files(d.get("files")), str(d.get("name", ""))
    name = (request.form.get("name") or (request.get_json(silent=True) or {}).get("name") or name or "Untitled project")[:60]
    pid = uuid.uuid4().hex[:10]
    langs = [xray.language_of(p) for p in files if xray.language_of(p) in xray.CODE]
    entry = next((p for p in ("main.py", "app.py", "index.js", "main.js", "server.js") if p in files), "")
    c.execute("INSERT INTO projects(id,owner,name,created,updated,entry) VALUES(?,?,?,?,?,?)", (pid, owner(), name, time.time(), time.time(), entry))
    c.commit()
    sid = add_snapshot(pid, "Original upload", files)
    bump("projects_created")
    return jsonify(id=pid, name=name, files=len(files), skipped=skipped, snapshot=sid, entry=entry, languages=sorted(set(langs)))


@bp.route("/api/projects/<pid>")
def get_project(pid):
    row = project_row(pid)
    files, sid = snap_files(pid)
    return jsonify(id=pid, name=row["name"], entry=row["entry"], tests=_tests(row), snapshot=sid,
                   snapshots=[{"id": s["id"], "label": s["label"], "ts": s["ts"], "size": s["size"]} for s in snap_list(pid)],
                   files=[{"path": p, "lines": t.count("\n") + 1, "size": len(t), "lang": xray.language_of(p)} for p, t in sorted(files.items())],
                   memory=json.loads(row["memory_json"] or "[]"))


@bp.route("/api/projects/<pid>", methods=["DELETE"])
def delete_project(pid):
    project_row(pid)
    c = accounts.db()
    c.execute("DELETE FROM snapshots WHERE project_id=?", (pid,))
    c.execute("DELETE FROM projects WHERE id=?", (pid,))
    c.commit()
    return jsonify(ok=True)


@bp.route("/api/projects/<pid>/file")
def get_file(pid):
    project_row(pid)
    files, _ = snap_files(pid, request.args.get("snapshot", type=int))
    p = request.args.get("path", "")
    if p not in files:
        raise ApiError("File not found in that version.", 404)
    return jsonify(path=p, content=files[p])


@bp.route("/api/projects/<pid>/snapshot", methods=["POST"])
def save_snapshot(pid):
    project_row(pid)
    d = request.get_json(silent=True) or {}
    files, _ = snap_files(pid)
    for k, v in (d.get("changes") or {}).items():
        k = sandbox.safe_rel(k)
        if v is None:
            files.pop(k, None)
        else:
            files[k] = v
    files = clean_files(files)
    sid = add_snapshot(pid, str(d.get("label") or "Saved version"), files)
    return jsonify(snapshot=sid)


@bp.route("/api/projects/<pid>/restore", methods=["POST"])
def restore(pid):
    project_row(pid)
    target = (request.get_json(silent=True) or {}).get("snapshot")
    files, sid = snap_files(pid, int(target or 0))
    return jsonify(snapshot=add_snapshot(pid, f"Rollback to version #{sid}", files))


@bp.route("/api/projects/<pid>/settings", methods=["PUT"])
def settings(pid):
    project_row(pid)
    d = request.get_json(silent=True) or {}
    entry = str(d.get("entry", "")).strip()
    if entry:
        try:
            entry = sandbox.safe_rel(entry)
        except sandbox.SandboxError as e:
            raise ApiError(str(e))
    tests = []
    for t in (d.get("tests") or [])[:8]:
        if isinstance(t, dict):
            tests.append({"name": str(t.get("name", ""))[:60], "stdin": str(t.get("stdin", ""))[:2000], "expected": str(t.get("expected", ""))[:2000]})
    c = accounts.db()
    c.execute("UPDATE projects SET entry=?, tests_json=? WHERE id=?", (entry, json.dumps(tests), pid))
    c.commit()
    return jsonify(entry=entry, tests=tests)


@bp.route("/api/projects/<pid>/tests/run", methods=["POST"])
def tests_run(pid):
    row = project_row(pid)
    tests = _tests(row)
    if not tests:
        raise ApiError("Add at least one recorded test (input + expected output) first.")
    _gate("run", len(tests))
    files, sid = snap_files(pid, (request.get_json(silent=True) or {}).get("snapshot"))
    try:
        res = run_project_tests(files, row["entry"], tests)
    except sandbox.SandboxError as e:
        raise ApiError(str(e), 503)
    bump("project_test_runs")
    return jsonify(snapshot=sid, **res)


@bp.route("/api/projects/<pid>/tests/record", methods=["POST"])
def tests_record(pid):
    """Record the CURRENT behaviour of a version as the expected output ("it worked back then")."""
    row = project_row(pid)
    d = request.get_json(silent=True) or {}
    inputs = [str(x)[:2000] for x in (d.get("inputs") or [""])][:8]
    _gate("run", len(inputs))
    files, sid = snap_files(pid, d.get("snapshot"))
    if not row["entry"] or row["entry"] not in files:
        raise ApiError("Set the entry file first.")
    lang, tests = xray.language_of(row["entry"]), []
    try:
        for i, inp in enumerate(inputs, 1):
            r = sandbox.execute_files(lang, files, row["entry"], inp)
            if r["status"] != "ok":
                raise ApiError(f"Test input {i} did not run cleanly in version #{sid} ({r['status'].replace('_', ' ')}): {r['stderr'][:200]}")
            tests.append({"name": f"Recorded #{i} (v{sid})", "stdin": inp, "expected": r["stdout"].strip()})
    except sandbox.SandboxError as e:
        raise ApiError(str(e), 503)
    c = accounts.db()
    c.execute("UPDATE projects SET tests_json=? WHERE id=?", (json.dumps(tests), pid))
    c.commit()
    return jsonify(tests=tests, snapshot=sid)


# ───────────────────────── X-Ray ─────────────────────────
@bp.route("/api/projects/<pid>/xray")
def xray_view(pid):
    project_row(pid)
    files, idx, sid = get_index(pid, request.args.get("snapshot", type=int))
    if (pid, sid, "an") not in _cache:                                   # duplicates / unused are cached per version too
        _cache[(pid, sid, "an")] = (xray.duplicates(files, idx), xray.unused(files, idx))
    dups, unu = _cache[(pid, sid, "an")]
    evidence = [ev(f"{len(dups)} duplicate-logic group(s) found", "static", [item("code_location", f"{i['path']}:{i['start']}-{i['end']}", i["name"]) for g in dups[:2] for i in g["items"]], "X-Ray")] if dups else []
    if unu:
        evidence.append(ev(f"{len(unu)} function(s) look unused", "static", [item("code_location", f"{u['path']}:{u['line']}", u["why"]) for u in unu[:5]], "X-Ray"))
        evidence.append(ev("Unused-code results may include code used dynamically", "uncertain", [item("note", "caveat", "Reflection, decorators, string-based dispatch and tests outside the project are invisible to static analysis.")], "X-Ray"))
    if idx["errors"]:
        evidence.append(ev(f"{len(idx['errors'])} file(s) failed to parse", "static", [item("parse_error", e["path"], e["error"]) for e in idx["errors"][:5]], "X-Ray"))
    return jsonify(snapshot=sid, summary=xray.summary(idx), graph=xray.graph(idx), duplicates=dups, unused=unu, endpoints=idx["endpoints"], external=idx["external"],
                   errors=idx["errors"], functions=[{"path": f["path"], "name": f["qualname"], "line": f["line"], "end": f["end"], "cc": f["cc"]} for f in idx["functions"]][:600],
                   files=list(idx["files"].values()), evidence=evidence)


@bp.route("/api/projects/<pid>/search")
def xray_search(pid):
    project_row(pid)
    files, idx, _ = get_index(pid)
    return jsonify(results=xray.search(files, idx, request.args.get("q", "")[:200]))


@bp.route("/api/projects/<pid>/ask", methods=["POST"])
def ask(pid):
    row = project_row(pid)
    q = str((request.get_json(silent=True) or {}).get("question", "")).strip()[:600]
    if not q:
        raise ApiError("Type a question about the project.")
    key = _key()
    _gate("ai")
    files, idx, _ = get_index(pid)
    ctx, sources = xray.retrieve(files, idx, q)
    mem = memory_text(row)
    system = ("You answer questions about the user's codebase using ONLY the excerpts provided. Cite files as path:line. If the excerpts do not contain the answer, "
              "say what is missing instead of guessing. Be concise." + (f"\nProject memory approved by the user:\n{mem}" if mem else ""))

    def gen():
        yield {"sources": sources}
        r, err = open_stream(key, system, [{"role": "user", "content": f"{ctx}\n\nQUESTION: {q}"}])
        if err:
            raise ApiError(err, 502)
        r.encoding = "utf-8"
        try:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    p = line[5:].strip()
                    if p == "[DONE]":
                        break
                    try:
                        dl = json.loads(p)["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if dl:
                        yield {"d": dl}
        finally:
            r.close()
        bump("project_questions")
    return ndjson(gen)


# ───────────────────────── Change Impact Radar ─────────────────────────
@bp.route("/api/projects/<pid>/impact", methods=["POST"])
def impact_view(pid):
    row = project_row(pid)
    d = request.get_json(silent=True) or {}
    files, idx, _ = get_index(pid)
    try:
        res = xray.impact(files, idx, str(d.get("path", "")), d.get("function") or None, d.get("new_code") or None, _tests(row), row["entry"])
    except ValueError as e:
        raise ApiError(str(e))
    evidence = [ev(f"Risk level: {res['level'].upper()} (score {res['score']})", "static", [item("factor", f["name"], "; ".join(f["evidence"][:3])) for f in res["factors"]], "Impact Radar")]
    for b in res["breaking"]:
        evidence.append(ev("Breaking API change: " + b["detail"], "static", [item("signature", res["function"] or res["path"], b["type"])], "Impact Radar"))
    if res["approximate_callers"]:
        evidence.append(ev("Caller list is approximate for this language", "uncertain", [item("note", "caveat", res["note"])], "Impact Radar"))
    res["evidence"] = evidence
    bump("impact_runs")
    return jsonify(res)


# ───────────────────────── Time Machine ─────────────────────────
@bp.route("/api/projects/<pid>/compare", methods=["POST"])
def compare(pid):
    project_row(pid)
    d = request.get_json(silent=True) or {}
    a, _ = snap_files(pid, d.get("a"))
    b, _ = snap_files(pid, d.get("b"))
    out = []
    for p in sorted(set(a) | set(b)):
        if p in a and p in b and a[p] == b[p]:
            continue
        adds, dels = line_stats(a.get(p, ""), b.get(p, ""))
        out.append({"path": p, "status": "added" if p not in a else "removed" if p not in b else "modified", "adds": adds, "dels": dels})
    return jsonify(changed=out[:300], total=len(out))


@bp.route("/api/projects/<pid>/timemachine/scan", methods=["POST"])
def tm_scan(pid):
    row = project_row(pid)
    tests = _tests(row)
    if not tests:
        raise ApiError("Add recorded test inputs first (or record them from a version that worked).")
    snaps = snap_list(pid)[-15:]
    if len(snaps) < 2:
        raise ApiError("A project needs at least two versions to compare. Save a new version after changing the code.")
    _gate("run", len(snaps) * len(tests))

    def gen():
        matrix = []
        for s in snaps:
            yield {"step": "run", "msg": f"Running {len(tests)} test(s) on version #{s['id']} — {s['label']}"}
            files, _ = snap_files(pid, s["id"])
            try:
                res = run_project_tests(files, row["entry"], tests)
            except sandbox.SandboxError as e:
                raise ApiError(str(e), 503)
            row_ = {"snapshot": s["id"], "label": s["label"], "ts": s["ts"], "passed": res["passed"], "total": res["total"], "compile_error": bool(res.get("compile_error")), "results": res.get("results", [])}
            if res.get("compile_error"):
                row_["stderr"] = res["compile_error"]["stderr"][:600]
            matrix.append(row_)
            yield {"row": {k: row_[k] for k in ("snapshot", "label", "passed", "total", "compile_error")}}
        ok = lambda m: m["passed"] == m["total"] and not m["compile_error"]
        first_bad = last_good = None
        for i, m in enumerate(matrix):
            if not ok(m):
                if i > 0 and ok(matrix[i - 1]):
                    first_bad, last_good = m["snapshot"], matrix[i - 1]["snapshot"]
                    break
        if first_bad is None and ok(matrix[0]) and not ok(matrix[-1]):
            idx = next(i for i, m in enumerate(matrix) if not ok(m))
            first_bad, last_good = matrix[idx]["snapshot"], matrix[idx - 1]["snapshot"]
        latest_ok = ok(matrix[-1])
        verdict = ("regression" if first_bad else "no_failure" if latest_ok else "always_failing")
        evidence = [ev(f"Version #{m['snapshot']} ({m['label']}): {m['passed']}/{m['total']} tests pass", "verified",
                       [item("execution", f"{r['name']}", f"{'PASS' if r['passed'] else 'FAIL'} — actual {r['actual'][:80]!r}") for r in m["results"][:4]], "Time Machine") for m in matrix[-6:]]
        bump("timemachine_scans")
        yield {"done": {"matrix": matrix, "first_bad": first_bad, "last_good": last_good, "verdict": verdict, "evidence": evidence}}
    return ndjson(gen)


TM_SYS = ("You are a debugging assistant. A project's recorded tests passed in an older version and fail in a newer one. You get the unified diff between the two "
          "versions and the failing test (input, expected, actual output, error). Reply with ONE JSON object: {\"root_cause\": string, \"suspects\": [{\"path\": string, "
          "\"line_start\": int, \"line_end\": int, \"reason\": string}], \"fix\": {\"path\": string, \"new_content\": string (the COMPLETE corrected file), \"explanation\": string}}. "
          "Line numbers refer to the NEW version of the file. Prefer the smallest possible change that restores the old behaviour.")


@bp.route("/api/projects/<pid>/timemachine/analyze", methods=["POST"])
def tm_analyze(pid):
    row = project_row(pid)
    d = request.get_json(silent=True) or {}
    key = _key()
    tests = _tests(row)
    if not tests:
        raise ApiError("Add recorded test inputs first.")
    _gate("ai")
    _gate("run", len(tests) * 2)
    good, _ = snap_files(pid, d.get("good"))
    bad, bad_id = snap_files(pid, d.get("bad"))

    def gen():
        yield {"step": "diff", "msg": "Comparing the two versions…"}
        diffs = {p: udiff(p, good.get(p, ""), bad.get(p, "")) for p in sorted(set(good) | set(bad)) if good.get(p) != bad.get(p)}
        if not diffs:
            raise ApiError("These two versions are identical.")
        yield {"step": "replay", "msg": f"Replaying the recorded inputs on version #{bad_id}…"}
        try:
            res = run_project_tests(bad, row["entry"], tests)
        except sandbox.SandboxError as e:
            raise ApiError(str(e), 503)
        failing = [r for r in res.get("results", []) if not r["passed"]][:2]
        if res.get("compile_error"):
            failing = [{"name": "build", "stdin": "", "expected": "", "actual": "", "stderr": res["compile_error"]["stderr"][:800]}]
        if not failing:
            raise ApiError("All recorded tests pass on that version — there is no failure to replay.")
        yield {"step": "ai", "msg": "Asking the AI to find the suspicious change…"}
        mem = memory_text(row)
        prompt = ("DIFF (old → new):\n" + "\n".join(diffs.values())[:18000] + "\n\nFAILING TESTS:\n" + json.dumps(failing)[:5000]
                  + (f"\n\nPROJECT MEMORY:\n{mem}" if mem else "") + "\n\nCURRENT FILES CHANGED:\n" + "\n".join(f"### {p}\n{bad.get(p, '')[:8000]}" for p in list(diffs)[:3] if p in bad))
        txt, err = complete(key, TM_SYS, prompt, max_tokens=4000, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        suspects = []
        for s in (obj.get("suspects") or [])[:5]:
            try:
                p, a, b = str(s["path"]), int(s["line_start"]), int(s.get("line_end", s["line_start"]))
            except (KeyError, TypeError, ValueError):
                continue
            ch = changed_lines(good.get(p, ""), bad.get(p, "")) if p in bad else set()
            line_text = (bad.get(p, "").split("\n") + [""] * 1)[a - 1] if p in bad and 0 < a <= bad[p].count("\n") + 1 else ""
            suspects.append({"path": p, "line_start": a, "line_end": b, "reason": str(s.get("reason", ""))[:300], "in_changed_lines": bool(ch & set(range(a, b + 1))), "line_text": line_text.strip()[:200]})
        fix = None
        f = obj.get("fix") or {}
        if isinstance(f.get("path"), str) and isinstance(f.get("new_content"), str) and f["path"] in bad and len(f["new_content"]) < MAX_FILE:
            yield {"step": "verify", "msg": "Checking the proposed fix by running the tests…"}
            fixed = dict(bad)
            fixed[f["path"]] = f["new_content"]
            try:
                fr = run_project_tests(fixed, row["entry"], tests)
            except sandbox.SandboxError as e:
                raise ApiError(str(e), 503)
            fix = {"path": f["path"], "new_content": f["new_content"], "diff": udiff(f["path"], bad[f["path"]], f["new_content"]), "explanation": str(f.get("explanation", ""))[:500],
                   "passed": fr["passed"], "total": fr["total"], "verified": not fr.get("compile_error") and fr["passed"] == fr["total"],
                   "adds_dels": line_stats(bad[f["path"]], f["new_content"])}
        evidence = [ev("Failing behaviour reproduced with the recorded inputs", "verified", [item("execution", r["name"], f"expected {r['expected'][:60]!r}, got {r['actual'][:60]!r} {r['stderr'][:80]}") for r in failing], "Time Machine")]
        for s in suspects:
            evidence.append(ev(f"Suspect: {s['path']}:{s['line_start']}-{s['line_end']} — {s['reason']}", "ai",
                               [item("code_location", f"{s['path']}:{s['line_start']}", s["line_text"] or "(line not found)"), item("static_check", "diff", "inside the lines that changed" if s["in_changed_lines"] else "NOT inside the changed lines — treat with suspicion")], "Time Machine"))
        if fix:
            evidence.append(ev(f"Proposed fix passes {fix['passed']}/{fix['total']} recorded tests", "verified" if fix["verified"] else "uncertain",
                               [item("execution", "re-run", f"{fix['passed']}/{fix['total']} pass after applying the fix")], "Time Machine"))
        bump("timemachine_analyses")
        yield {"done": {"root_cause": str(obj.get("root_cause", ""))[:600], "suspects": suspects, "fix": fix, "failing": failing, "changed_files": list(diffs), "diffs": {k: v[:6000] for k, v in diffs.items()}, "evidence": evidence}}
    return ndjson(gen)


# ───────────────────────── Mission Mode ─────────────────────────
def _overview(files, idx):
    lines = []
    for p, f in sorted(idx["files"].items())[:80]:
        syms = [x["qualname"] for x in idx["functions"] if x["path"] == p][:8]
        lines.append(f"{p} [{f['lang']}, {f['lines']} lines]" + (": " + ", ".join(syms) if syms else ""))
    return "\n".join(lines)[:6000]


PLAN_SYS = ("You are a senior engineer planning a coding task on an existing project. Reply with ONE JSON object: {\"summary\": string, \"steps\": [{\"title\": string, "
            "\"description\": string, \"files\": [paths to change or create]}], \"risks\": [string]}. Use at most 6 small, ordered steps. Only reference files that exist "
            "or that you will create. Prefer changing existing files over adding new ones.")
PROPOSE_SYS = ("You are a senior engineer implementing ONE step of a plan. Reply with ONE JSON object: {\"changes\": [{\"path\": string, \"action\": \"modify\"|\"create\"|\"delete\", "
               "\"content\": string, \"explanation\": string}], \"notes\": string, \"unfinished\": [string]}. For modify and create, content is the COMPLETE new file. Change as little as possible, "
               "keep the existing style, never touch files you do not need. List anything this step does not cover in unfinished.")


@bp.route("/api/projects/<pid>/mission/plan", methods=["POST"])
def mission_plan(pid):
    row = project_row(pid)
    task = str((request.get_json(silent=True) or {}).get("task", "")).strip()[:1500]
    if len(task) < 8:
        raise ApiError("Describe the task in a sentence or two.")
    key = _key()
    _gate("ai")
    files, idx, _ = get_index(pid)
    ctx, _src = xray.retrieve(files, idx, task, budget=14000)
    mem = memory_text(row)

    def gen():
        yield {"step": "plan", "msg": "Reading the project and planning…"}
        txt, err = complete(key, PLAN_SYS, f"TASK: {task}\n\nPROJECT OVERVIEW:\n{_overview(files, idx)}\n\nRELEVANT CODE:\n{ctx}" + (f"\n\nPROJECT MEMORY:\n{mem}" if mem else ""), max_tokens=2500, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        steps = []
        for i, s in enumerate((obj.get("steps") or [])[:6], 1):
            if not isinstance(s, dict) or not s.get("title"):
                continue
            fl = []
            for p in s.get("files") or []:
                try:
                    fl.append(sandbox.safe_rel(p))
                except sandbox.SandboxError:
                    pass
            steps.append({"id": i, "title": str(s["title"])[:120], "description": str(s.get("description", ""))[:500], "files": fl[:6], "new_files": [p for p in fl if p not in files][:6]})
        if not steps:
            raise ApiError("The AI did not produce a usable plan. Rephrase the task and try again.", 502)
        bump("missions_planned")
        yield {"done": {"summary": str(obj.get("summary", ""))[:500], "steps": steps, "risks": [str(r)[:200] for r in (obj.get("risks") or [])[:5]]}}
    return ndjson(gen)


@bp.route("/api/projects/<pid>/mission/propose", methods=["POST"])
def mission_propose(pid):
    row = project_row(pid)
    d = request.get_json(silent=True) or {}
    task, step, failure = str(d.get("task", ""))[:1500], d.get("step") or {}, str(d.get("failure", ""))[:2500]
    if not isinstance(step, dict) or not step.get("title"):
        raise ApiError("Missing step.")
    key = _key()
    _gate("ai")
    files, idx, _ = get_index(pid)
    ctx_files, used = [], 0
    for p in [x for x in (step.get("files") or []) if x in files][:6]:
        body = files[p][:20000]
        used += len(body)
        if used > 42000:
            break
        ctx_files.append(f"### {p}\n{body}")
    extra, _src = xray.retrieve(files, idx, task + " " + step.get("title", ""), budget=9000)
    mem = memory_text(row)

    def gen():
        yield {"step": "propose", "msg": f"Writing the changes for: {step['title']}"}
        prompt = (f"OVERALL TASK: {task}\nTHIS STEP: {step['title']} — {step.get('description', '')}\n\nCURRENT FILES:\n" + "\n\n".join(ctx_files) + f"\n\nOTHER CONTEXT:\n{extra}"
                  + (f"\n\nPROJECT MEMORY:\n{mem}" if mem else "") + (f"\n\nTHE PREVIOUS ATTEMPT FAILED:\n{failure}\nFix it." if failure else ""))
        txt, err = complete(key, PROPOSE_SYS, prompt, max_tokens=6000, json_mode=True)
        if err:
            raise ApiError(err, 502)
        obj = extract_json(txt) or {}
        changes = []
        for c in (obj.get("changes") or [])[:8]:
            try:
                p = sandbox.safe_rel(c.get("path"))
            except sandbox.SandboxError:
                continue
            action, content = c.get("action"), c.get("content") if isinstance(c.get("content"), str) else ""
            if action not in ("modify", "create", "delete") or len(content) > MAX_FILE:
                continue
            if action == "modify" and p not in files:
                action = "create"
            if action == "create" and p in files:
                action = "modify"
            if action == "delete" and p not in files:
                continue
            old = files.get(p, "")
            new = "" if action == "delete" else content
            adds, dels = line_stats(old, new)
            err_ = None
            if p.endswith(".py") and action != "delete":
                import ast
                try:
                    ast.parse(new)
                except SyntaxError as e:
                    err_ = f"Python syntax error at line {e.lineno}: {e.msg}"
            changes.append({"path": p, "action": action, "content": new, "explanation": str(c.get("explanation", ""))[:400], "diff": udiff(p, old, new),
                            "adds": adds, "dels": dels, "syntax_error": err_, "unplanned": p not in (step.get("files") or [])})
        if not changes:
            raise ApiError("The AI did not propose any usable change for this step. Try again or refine the task.", 502)
        bump("mission_proposals")
        yield {"done": {"changes": changes, "notes": str(obj.get("notes", ""))[:600], "unfinished": [str(u)[:200] for u in (obj.get("unfinished") or [])[:6]]}}
    return ndjson(gen)


@bp.route("/api/projects/<pid>/mission/apply", methods=["POST"])
def mission_apply(pid):
    row = project_row(pid)
    d = request.get_json(silent=True) or {}
    changes = d.get("changes")
    if not isinstance(changes, list) or not changes or len(changes) > 8:
        raise ApiError("Nothing to apply.")
    files, base_id = snap_files(pid)
    new = dict(files)
    for c in changes:
        try:
            p = sandbox.safe_rel(c.get("path"))
        except sandbox.SandboxError as e:
            raise ApiError(str(e))
        if c.get("action") == "delete":
            new.pop(p, None)
        elif c.get("action") in ("modify", "create") and isinstance(c.get("content"), str):
            new[p] = c["content"]
        else:
            raise ApiError("Invalid change.")
    new = clean_files(new)
    tests = _tests(row)
    if tests:
        _gate("run", len(tests))

    def gen():
        yield {"step": "apply", "msg": "Saving a new version…"}
        sid = add_snapshot(pid, "Mission: " + str(d.get("label") or "applied changes")[:60], new)
        syntax = []
        for p, t in new.items():
            if p.endswith(".py") and files.get(p) != t:
                import ast
                try:
                    ast.parse(t)
                except SyntaxError as e:
                    syntax.append({"path": p, "error": f"line {e.lineno}: {e.msg}"})
        res = None
        if tests and row["entry"] in new:
            yield {"step": "test", "msg": f"Running {len(tests)} recorded test(s)…"}
            try:
                res = run_project_tests(new, row["entry"], tests)
            except sandbox.SandboxError as e:
                res = {"error": str(e)}
        evidence = []
        if res and "passed" in res:
            evidence.append(ev(f"After the change: {res['passed']}/{res['total']} recorded tests pass", "verified",
                               [item("execution", r["name"], f"{'PASS' if r['passed'] else 'FAIL'} actual {r['actual'][:60]!r}") for r in res["results"][:5]], "Mission"))
        for s in syntax:
            evidence.append(ev(f"Syntax error in {s['path']}", "verified", [item("compiler", s["path"], s["error"])], "Mission"))
        evidence.append(ev("The patch itself was written by the AI and approved by you", "ai", [item("note", "approval", f"{len(changes)} file(s) changed")], "Mission"))
        bump("mission_applied")
        yield {"done": {"snapshot": sid, "previous": base_id, "tests": res, "syntax_errors": syntax, "evidence": evidence}}
    return ndjson(gen)


# ───────────────────────── project Memory ─────────────────────────
KINDS = {"architecture", "convention", "dependency", "fix", "note"}


@bp.route("/api/projects/<pid>/memory", methods=["PUT"])
def memory_save(pid):
    project_row(pid)
    out = []
    for e in (request.get_json(silent=True) or {}).get("entries", [])[:60]:
        if isinstance(e, dict) and e.get("kind") in KINDS and str(e.get("text", "")).strip():
            out.append({"id": str(e.get("id") or uuid.uuid4().hex[:8])[:12], "kind": e["kind"], "text": str(e["text"]).strip()[:300], "approved": bool(e.get("approved")),
                        "source": "user" if e.get("source") == "user" else "auto", "ts": e.get("ts") or time.time()})
    c = accounts.db()
    c.execute("UPDATE projects SET memory_json=? WHERE id=?", (json.dumps(out), pid))
    c.commit()
    return jsonify(entries=out)


MEM_SYS = ("From the project overview and README, list up to 5 durable ARCHITECTURE DECISIONS or constraints a future developer must respect (for example the chosen framework, "
           "data flow, folder layout rules). Reply with ONE JSON object: {\"entries\": [{\"kind\": \"architecture\"|\"convention\"|\"note\", \"text\": string (one sentence)}]}. "
           "Only state things visible in the material provided.")


@bp.route("/api/projects/<pid>/memory/suggest", methods=["POST"])
def memory_suggest(pid):
    project_row(pid)
    files, idx, _ = get_index(pid)
    out = [{"id": uuid.uuid4().hex[:8], "kind": c["kind"], "text": c["text"], "approved": False, "source": "auto", "evidence": c["evidence"], "origin": "static analysis"} for c in xray.conventions(files, idx)]
    if (request.get_json(silent=True) or {}).get("use_ai"):
        key = _key()
        _gate("ai")
        readme = next((files[p] for p in files if p.lower().startswith("readme")), "")
        txt, err = complete(key, MEM_SYS, f"OVERVIEW:\n{_overview(files, idx)}\n\nREADME:\n{readme[:4000]}", max_tokens=900, json_mode=True)
        if not err:
            for e in (extract_json(txt) or {}).get("entries", [])[:5]:
                if isinstance(e, dict) and e.get("kind") in KINDS and e.get("text"):
                    out.append({"id": uuid.uuid4().hex[:8], "kind": e["kind"], "text": str(e["text"])[:300], "approved": False, "source": "auto", "evidence": "AI reading of README + file overview", "origin": "AI"})
    return jsonify(suggestions=out)
