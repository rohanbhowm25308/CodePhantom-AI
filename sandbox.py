"""Isolated code execution for CODEPHANTOM AI.

Two back-ends, chosen automatically:
  1. Judge0   -> set JUDGE0_URL (and JUDGE0_TOKEN if your instance needs one)
  2. Docker   -> every run is a throw-away container with NO network, capped CPU / memory /
                 processes / time, read-only root filesystem, all capabilities dropped and an
                 unprivileged user. Code never runs inside the Flask process.
If neither is available, running is disabled and the UI says so.
"""
import base64
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import requests

RUN_TIMEOUT = int(os.getenv("RUN_TIMEOUT", "5"))        # seconds of program run time
RUN_MEMORY = os.getenv("RUN_MEMORY", "512m")
MAX_OUT = 20_000                                        # chars of stdout / stderr returned
MAX_CODE = 50_000
MAX_STDIN = 10_000
_tl = threading.local()
_slots = threading.BoundedSemaphore(int(os.getenv("RUN_CONCURRENCY", "2")))

# key = lower-case UI language name
DOCKER_LANGS = {
    "python":     dict(img="python:3.12-alpine",       file="main.py",   run="python3 main.py"),
    "javascript": dict(img="node:20-alpine",           file="main.js",   run="node main.js"),
    "c":          dict(img="gcc:13",                   file="main.c",    build="gcc -O2 -o main main.c -lm", run="./main"),
    "c++":        dict(img="gcc:13",                   file="main.cpp",  build="g++ -O2 -std=c++17 -o main main.cpp", run="./main"),
    "java":       dict(img="eclipse-temurin:21-jdk-alpine", file="Main.java", build="javac Main.java", run="java -Xmx192m -Xss512k Main"),
    "go":         dict(img="golang:1.22-alpine",       file="main.go",   build="go build -o main main.go", run="./main"),
    "rust":       dict(img="rust:1.80-alpine",         file="main.rs",   build="rustc -O -o main main.rs", run="./main"),
    "php":        dict(img="php:8.3-cli-alpine",       file="main.php",  run="php main.php"),
    "ruby":       dict(img="ruby:3.3-alpine",          file="main.rb",   run="ruby main.rb"),
    "bash":       dict(img="bash:5.2",                 file="main.sh",   run="bash main.sh"),
}
JUDGE0_IDS = {"python": 71, "javascript": 63, "c": 50, "c++": 54, "java": 62, "go": 60, "rust": 73, "php": 68,
              "ruby": 72, "bash": 46, "typescript": 74, "c#": 51, "kotlin": 78, "swift": 83, "r": 80, "sql": 82}


# ── Local runner: for developing on YOUR OWN machine only. NOT isolated (no memory cap, no network block). ──
EXE = ".exe" if os.name == "nt" else ""
LOCAL_LANGS = {   # tool that must exist on PATH (None = always available)
    "python":     dict(tool=None,    file="main.py",   run=[sys.executable, "main.py"]),
    "javascript": dict(tool="node",  file="main.js",   run=["node", "main.js"]),
    "c":          dict(tool="gcc",   file="main.c",    build=["gcc", "-O2", "-o", "main" + EXE, "main.c", "-lm"], run=["{exe}"]),
    "c++":        dict(tool="g++",   file="main.cpp",  build=["g++", "-O2", "-std=c++17", "-o", "main" + EXE, "main.cpp"], run=["{exe}"]),
    "java":       dict(tool="javac", file="Main.java", build=["javac", "Main.java"], run=["java", "-Xmx256m", "Main"], also="java"),
    "go":         dict(tool="go",    file="main.go",   build=["go", "build", "-o", "main" + EXE, "main.go"], run=["{exe}"]),
    "rust":       dict(tool="rustc", file="main.rs",   build=["rustc", "-O", "-o", "main" + EXE, "main.rs"], run=["{exe}"]),
    "php":        dict(tool="php",   file="main.php",  run=["php", "main.php"]),
    "ruby":       dict(tool="ruby",  file="main.rb",   run=["ruby", "main.rb"]),
    "bash":       dict(tool="bash",  file="main.sh",   run=["bash", "main.sh"]),
}


def _local_allowed() -> bool:
    flag = os.getenv("LOCAL_RUN", "auto").lower()
    if flag in ("0", "false", "off"):
        return False
    if flag in ("1", "true", "on"):
        return True
    return (os.getenv("APP_ENV", "development").lower() != "production"
            and os.getenv("HOST", "127.0.0.1") in ("127.0.0.1", "localhost"))


def _local_langs():
    return sorted(k for k, v in LOCAL_LANGS.items()
                  if (v["tool"] is None or shutil.which(v["tool"])) and (not v.get("also") or shutil.which(v["also"])))


def _safe_env(work):
    """Child gets no secrets: GROQ_API_KEY and the rest of your environment are NOT passed on."""
    keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "LANG") if k in os.environ}
    keep.update(TEMP=work, TMP=work, TMPDIR=work, HOME=work, USERPROFILE=work, GOCACHE=os.path.join(work, ".gocache"),
                PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return keep


def _run_local(lang, code, stdin, files=None, entry=None):
    spec = LOCAL_LANGS[lang]
    work = tempfile.mkdtemp(prefix="cp_")
    env = _safe_env(work)
    exe = os.path.join(work, "main" + EXE)
    fix = lambda cmd: [exe if c == "{exe}" else c for c in cmd]
    try:
        main = entry or spec["file"]
        for rel, text in (files or {main: code}).items():
            dest = os.path.join(work, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
        start = time.time()
        if spec.get("build"):
            try:
                b = subprocess.run(spec["build"], cwd=work, env=env, capture_output=True, timeout=40)
            except subprocess.TimeoutExpired:
                return dict(status="compile_error", stdout="", stderr="Compilation took too long.", exit_code=None, time_ms=None)
            if b.returncode != 0:
                msg = (b.stderr + b.stdout).decode("utf-8", "replace")
                return dict(status="compile_error", stdout="", stderr=_clip(msg), exit_code=b.returncode, time_ms=None)
        t0 = time.time()
        try:
            p = subprocess.run(fix([main if c == spec["file"] else c for c in spec["run"]]), cwd=work, env=env, input=stdin.encode(), capture_output=True, timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            return dict(status="timeout", stdout=_clip((e.stdout or b"").decode("utf-8", "replace")),
                        stderr=f"Time limit exceeded ({RUN_TIMEOUT} s).", exit_code=None, time_ms=int((time.time() - t0) * 1000))
        except OSError as e:
            raise SandboxError(f"Could not start the program: {e}")
        ms = int((time.time() - t0) * 1000)
        return dict(status="ok" if p.returncode == 0 else "runtime_error", stdout=_clip(p.stdout.decode("utf-8", "replace")),
                    stderr=_clip(p.stderr.decode("utf-8", "replace")), exit_code=p.returncode, time_ms=ms)
    finally:
        shutil.rmtree(work, ignore_errors=True)


class SandboxError(Exception):
    """Execution infrastructure problem (not a problem in the user's code)."""


_docker_cache = (0.0, False)


def _docker_ok() -> bool:
    global _docker_cache
    ts, ok = _docker_cache
    if time.time() - ts < 30:
        return ok
    ok = False
    if shutil.which("docker"):
        try:
            ok = subprocess.run(["docker", "info"], capture_output=True, timeout=6).returncode == 0
        except (subprocess.SubprocessError, OSError):
            ok = False
    _docker_cache = (time.time(), ok)
    return ok


def backend():
    if os.getenv("JUDGE0_URL"):
        return "judge0"
    if _docker_ok():
        return "docker"
    return "local" if _local_allowed() and _local_langs() else None


def languages():
    b = backend()
    if b == "judge0":
        return sorted(JUDGE0_IDS)
    if b == "docker":
        return sorted(DOCKER_LANGS)
    return _local_langs() if b == "local" else []


def _clip(text: str) -> str:
    limit = getattr(_tl, "limit", MAX_OUT)
    return text if len(text) <= limit else text[:limit] + "\n…[output truncated]"


# ───────────────────────── Docker ─────────────────────────
def _run_docker(spec, code, stdin, files=None, entry=None):
    work = tempfile.mkdtemp(prefix="cp_")
    name = "cp_" + uuid.uuid4().hex[:12]
    try:
        os.chmod(work, 0o777)
        main = entry or spec["file"]
        for rel, text in (files or {main: code}).items():
            dest = os.path.join(work, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
        for root_dir, dirs, _ in os.walk(work):
            for dname in dirs:
                os.chmod(os.path.join(root_dir, dname), 0o777)
        run_cmd = spec["run"].replace(spec["file"], shlex.quote(main))
        steps = []
        if spec.get("build"):
            steps.append(f"timeout 30 {spec['build']} 2>.build.log || {{ touch .build_failed; cat .build.log >&2; exit 1; }}")
        steps.append(f"timeout {RUN_TIMEOUT} {run_cmd}; rc=$?; case $rc in 124|137|143) touch .timed_out;; esac; exit $rc")
        cmd = [
            "docker", "run", "--rm", "-i", "--name", name, "--pull", "never",
            "--network", "none", "--memory", RUN_MEMORY, "--memory-swap", RUN_MEMORY, "--cpus", "1",
            "--pids-limit", "128", "--read-only", "--tmpfs", "/tmp:rw,exec,size=128m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--ulimit", "fsize=10485760", "--ulimit", "nofile=256",
            "-e", "HOME=/tmp", "-e", "GOCACHE=/tmp/gocache", "-e", "CARGO_HOME=/tmp/cargo",
            "-v", f"{work}:/work:rw", "-w", "/work", spec["img"], "sh", "-c", "\n".join(steps),
        ]
        start = time.time()
        try:
            p = subprocess.run(cmd, input=stdin.encode(), capture_output=True, timeout=RUN_TIMEOUT + 45)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return dict(status="timeout", stdout="", stderr=f"Time limit exceeded ({RUN_TIMEOUT} s).", exit_code=None,
                        time_ms=int((time.time() - start) * 1000))
        ms = int((time.time() - start) * 1000)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        if "Unable to find image" in err or "No such image" in err:
            raise SandboxError(f"Sandbox image {spec['img']} is missing. Run ./pull_images.sh once, then retry.")
        if p.returncode == 125:
            raise SandboxError("Docker could not start the sandbox: " + err.strip()[:300])
        if os.path.exists(os.path.join(work, ".build_failed")):
            status = "compile_error"
        elif os.path.exists(os.path.join(work, ".timed_out")):
            status, err = "timeout", (err + f"\nTime limit exceeded ({RUN_TIMEOUT} s).").strip()
        elif p.returncode == 0:
            status = "ok"
        elif p.returncode == 137:
            status, err = "killed", (err + "\nProcess was killed (likely out of memory).").strip()
        else:
            status = "runtime_error"
        return dict(status=status, stdout=_clip(out), stderr=_clip(err), exit_code=p.returncode, time_ms=ms)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ───────────────────────── Judge0 ─────────────────────────
def _b64(s): return base64.b64encode(s.encode()).decode()
def _unb64(s): return base64.b64decode(s).decode("utf-8", "replace") if s else ""


def _run_judge0(lang, code, stdin):
    headers = {"Content-Type": "application/json"}
    if os.getenv("JUDGE0_TOKEN"):
        headers["X-Auth-Token"] = os.getenv("JUDGE0_TOKEN")
    body = {"source_code": _b64(code), "language_id": JUDGE0_IDS[lang], "stdin": _b64(stdin),
            "cpu_time_limit": RUN_TIMEOUT, "wall_time_limit": RUN_TIMEOUT * 2, "memory_limit": 256000}
    try:
        r = requests.post(os.environ["JUDGE0_URL"].rstrip("/") + "/submissions?base64_encoded=true&wait=true",
                          json=body, headers=headers, timeout=RUN_TIMEOUT * 4 + 10)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        raise SandboxError(f"Judge0 request failed: {e}")
    sid = (d.get("status") or {}).get("id", 0)
    status = {3: "ok", 5: "timeout", 6: "compile_error"}.get(sid, "runtime_error" if sid >= 7 else "ok")
    err = _unb64(d.get("stderr")) or _unb64(d.get("message"))
    if status == "compile_error":
        err = _unb64(d.get("compile_output")) or err
    if sid == 5:
        err = f"Time limit exceeded ({RUN_TIMEOUT} s)."
    t = d.get("time")
    return dict(status=status, stdout=_clip(_unb64(d.get("stdout"))), stderr=_clip(err), exit_code=d.get("exit_code"),
                time_ms=int(float(t) * 1000) if t else None, memory_kb=d.get("memory"))


# ───────────────────────── public API ─────────────────────────
def execute(language: str, code: str, stdin: str = "", max_out: int = None, max_code: int = None):
    lang = language.strip().lower()
    b = backend()
    if not b:
        raise SandboxError("Code execution is not enabled on this server (install Docker or set JUDGE0_URL).")
    if lang not in languages():
        raise SandboxError(f"Running {language} is not enabled on this server.")
    if len(code) > (max_code or MAX_CODE):
        raise SandboxError("Code is too long to run.")
    if not _slots.acquire(timeout=10):
        raise SandboxError("The sandbox is busy. Try again in a few seconds.")
    _tl.limit = max_out or MAX_OUT
    try:
        if b == "judge0":
            return _run_judge0(lang, code, stdin[:MAX_STDIN])
        if b == "docker":
            return _run_docker(DOCKER_LANGS[lang], code, stdin[:MAX_STDIN])
        return _run_local(lang, code, stdin[:MAX_STDIN])
    finally:
        _slots.release()


# ───────────────────────── multi-file projects ─────────────────────────
MULTI_FILE = {"python", "javascript", "ruby", "php", "bash"}      # interpreted languages: all files are written, entry is run
MAX_PROJECT = 700_000


def safe_rel(p: str) -> str:
    """Normalise a project path; reject anything that could escape the work directory."""
    p = str(p).replace("\\", "/").strip().lstrip("/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts) or len("/".join(parts)) > 200 or ":" in parts[0]:
        raise SandboxError(f"Unsafe file path: {p!r}")
    return "/".join(parts)


def execute_files(language: str, files: dict, entry: str, stdin: str = "", max_out: int = None):
    """Run `entry` from a project. Interpreted languages see every file; compiled languages run the entry file alone."""
    lang = language.strip().lower()
    entry = safe_rel(entry)
    files = {safe_rel(k): v for k, v in files.items()}
    if entry not in files:
        raise SandboxError(f"Entry file {entry} is not in the project.")
    if sum(len(v) for v in files.values()) > MAX_PROJECT:
        raise SandboxError("The project is too large to run.")
    if lang not in MULTI_FILE or len(files) == 1:
        return execute(language, files[entry], stdin, max_out=max_out)
    b = backend()
    if not b or lang not in languages():
        raise SandboxError(f"Running {language} is not enabled on this server.")
    if b == "judge0":
        raise SandboxError("Running multi-file projects needs Docker or the local runner (Judge0 runs one file only).")
    if not _slots.acquire(timeout=10):
        raise SandboxError("The sandbox is busy. Try again in a few seconds.")
    _tl.limit = max_out or MAX_OUT
    try:
        if b == "docker":
            return _run_docker(DOCKER_LANGS[lang], files[entry], stdin[:MAX_STDIN], files=files, entry=entry)
        return _run_local(lang, files[entry], stdin[:MAX_STDIN], files=files, entry=entry)
    finally:
        _slots.release()
