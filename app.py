"""CODEPHANTOM AI — Flask backend.

Streaming Groq chat, sandboxed code execution, shared rate limits + daily quotas (Redis when REDIS_URL
is set), security headers, request logging and a token-protected metrics endpoint.
"""
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from werkzeug.middleware.proxy_fix import ProxyFix

import sandbox
from prompts import BASE, MODES

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile and llama-3.1-8b-instant on 16 Aug 2026. Retired names in .env are ignored.
RETIRED = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192", "llama3-8b-8192", "mixtral-8x7b-32768",
           "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"}
DEFAULT_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]
_cfg = os.getenv("GROQ_MODEL", "").strip()
MODEL_CHAIN = ([_cfg] if _cfg and _cfg not in RETIRED else []) + [m for m in DEFAULT_MODELS if m != _cfg]
MODEL = MODEL_CHAIN[0]
PRODUCTION = os.getenv("APP_ENV", "development").lower() == "production"
MAX_MESSAGES, MAX_TOTAL_CHARS = 12, 48_000
LIMITS = {  # (per-minute, per-day) per client IP
    "ai":  (int(os.getenv("AI_PER_MIN", 20)),  int(os.getenv("AI_PER_DAY", 300))),
    "run": (int(os.getenv("RUN_PER_MIN", 20)), int(os.getenv("RUN_PER_DAY", 400))),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("codephantom")

app = Flask(__name__, static_folder="static", static_url_path="", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024      # voice clips are the largest request
if os.getenv("TRUST_PROXY") == "1":          # only behind a reverse proxy you control (Render, nginx…)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ───────────── shared rate limiting / quotas ─────────────
_redis = None
if os.getenv("REDIS_URL"):
    try:
        import redis
        _redis = redis.from_url(os.environ["REDIS_URL"], socket_timeout=1.5)
        _redis.ping()
        log.info("Rate limiting: Redis (shared across workers)")
    except Exception as e:  # noqa: BLE001
        log.warning("Redis unavailable (%s) — falling back to per-process memory limits", e)
        _redis = None
elif PRODUCTION:
    log.warning("APP_ENV=production without REDIS_URL: rate limits are NOT shared between workers.")

_mem, _mem_lock = defaultdict(int), threading.Lock()


def take(bucket: str, ident: str, limit: int, window: int, cost: int = 1) -> bool:
    """Fixed-window counter. True while the caller is still within `limit`."""
    key = f"cp:{bucket}:{ident}:{int(time.time() // window)}"
    if _redis is not None:
        try:
            n = _redis.incrby(key, cost)
            if n == cost:
                _redis.expire(key, window + 5)
            return n <= limit
        except Exception:  # noqa: BLE001
            pass
    with _mem_lock:
        if len(_mem) > 20_000:
            _mem.clear()
        _mem[key] += cost
        return _mem[key] <= limit


def allowed(kind: str, cost: int = 1):
    """Returns None if OK, else an error message."""
    per_min, per_day = LIMITS[kind]
    ip = request.remote_addr or "?"
    if not take(kind + "m", ip, per_min, 60, cost):
        bump("rate_limited")
        return "Too many requests. Wait a minute and try again."
    if not take(kind + "d", ip, per_day, 86400, cost):
        bump("quota_exceeded")
        return "Daily usage limit reached. Come back tomorrow."
    return None


# ───────────── metrics / logging / headers ─────────────
_counters, _c_lock = defaultdict(int), threading.Lock()
_started = time.time()


def bump(name: str, n: int = 1):
    with _c_lock:
        _counters[name] += n


@app.before_request
def _t0():
    request._t = time.time()


@app.after_request
def _after(resp):
    resp.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    })
    if request.path.startswith("/frames/"):
        resp.headers["Cache-Control"] = "public, max-age=3600"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
        bump("requests_api")
        if resp.status_code >= 500:
            bump("errors_5xx")
        log.info("%s %s %s %dms ip=%s", request.method, request.path, resp.status_code,
                 (time.time() - getattr(request, "_t", time.time())) * 1000, request.remote_addr)
    return resp


@app.errorhandler(413)
def _too_big(_):
    return jsonify(error="Request too large."), 413


@app.errorhandler(404)
def _nf(_):
    return (jsonify(error="Not found."), 404) if request.path.startswith("/api/") else ("Not found", 404)


# ───────────── pages & health ─────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/api/health")
def health():
    return jsonify(ok=True, model=MODEL, key=bool(os.getenv("GROQ_API_KEY")))


@app.route("/api/metrics")
def metrics():
    token = os.getenv("METRICS_TOKEN")
    if not token:
        return jsonify(error="Not found."), 404
    if request.headers.get("Authorization") != f"Bearer {token}":
        return jsonify(error="Unauthorized."), 401
    with _c_lock:
        data = dict(_counters)
    return jsonify(uptime_s=int(time.time() - _started), counters=data, sandbox=sandbox.backend(),
                   shared_limits=_redis is not None, note="Counters are per worker process.")


# ───────────── AI chat (streamed) ─────────────
def groq_message(r) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return ""


def open_stream(key: str, system: str, messages: list):
    """Try each model in turn. Returns (response, error_message)."""
    err = "Groq returned an error."
    for model in MODEL_CHAIN:
        body = {"model": model, "stream": True, "temperature": 0.2, "max_tokens": 6000,
                "messages": [{"role": "system", "content": system}] + messages}
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"          # much faster first token
        try:
            r = requests.post(GROQ_URL, stream=True, timeout=(8, 60), json=body,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except requests.Timeout:
            return None, "The AI took too long to respond. Try again."
        except requests.RequestException:
            return None, "Could not reach Groq. Check your internet connection."
        if r.status_code == 200:
            return r, None
        detail = groq_message(r)
        r.close()
        log.warning("Groq %s on model %s: %s", r.status_code, model, detail)
        if r.status_code == 401:
            return None, "Groq rejected the API key. Check GROQ_API_KEY in .env (no quotes or spaces)."
        if r.status_code == 429:
            return None, "Groq rate limit reached. Wait a moment and retry."
        err = f"Groq error {r.status_code}: {detail or 'unknown error'}"
        if r.status_code in (400, 403, 404) and re.search(r"model|decommission|deprecat|not found|access", detail, re.I):
            continue                                  # try the next model
        break
    return None, err


@app.route("/api/chat", methods=["POST"])
def chat():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return jsonify(error="Server has no GROQ_API_KEY. Add it to the .env file and restart."), 503
    msg = allowed("ai")
    if msg:
        return jsonify(error=msg), 429

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "ask")
    language, target = str(data.get("language", ""))[:30], str(data.get("target", ""))[:30]
    raw = data.get("messages")
    if mode not in MODES or not isinstance(raw, list) or not raw:
        return jsonify(error="Invalid request."), 400
    messages = []
    for m in raw[-MAX_MESSAGES:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            return jsonify(error="Invalid message format."), 400
        messages.append({"role": m["role"], "content": m["content"]})
    if messages[-1]["role"] != "user":
        return jsonify(error="Last message must come from the user."), 400
    if sum(len(m["content"]) for m in messages) > MAX_TOTAL_CHARS:
        return jsonify(error="Input too long. Shorten the code or start a new mission."), 413

    system = BASE + "\n\n" + MODES[mode]
    if language:
        system += f"\nSelected language: {language}."
    if mode == "convert" and target:
        system += f" Target language: {target}."

    r, error = open_stream(key, system, messages)
    if error:
        return jsonify(error=error), 502
    r.encoding = "utf-8"
    bump("ai_requests")

    def generate():  # NDJSON: {"d": "text"} … or {"e": "error"}
        try:
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield json.dumps({"d": delta}) + "\n"
        except requests.RequestException:
            yield json.dumps({"e": "The connection to Groq was interrupted."}) + "\n"
        finally:
            r.close()

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson",
                    headers={"X-Accel-Buffering": "no"})


# ───────────── voice: Groq Whisper speech-to-text ─────────────
STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODELS = [m for m in (os.getenv("GROQ_STT_MODEL", "").strip(), "whisper-large-v3-turbo", "whisper-large-v3") if m]


@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return jsonify(error="Server has no GROQ_API_KEY. Add it to the .env file and restart."), 503
    msg = allowed("ai")
    if msg:
        return jsonify(error=msg), 429
    audio = request.get_data()
    if len(audio) < 1500:
        return jsonify(error="No audio was recorded. Check your microphone."), 400
    ctype = (request.mimetype or "audio/webm").lower()
    ext = "webm" if "webm" in ctype else "ogg" if "ogg" in ctype else "mp4" if ("mp4" in ctype or "m4a" in ctype) else "wav"
    lang = request.args.get("lang", "")[:5]
    error = "Speech recognition failed."
    for model in dict.fromkeys(STT_MODELS):
        data = {"model": model, "response_format": "json", "temperature": "0",
                "prompt": "A spoken programming request: code, function, array, string, loop, Python, C++, Java, JavaScript, SQL."}
        if lang.isalpha():
            data["language"] = lang.lower()
        try:
            r = requests.post(STT_URL, headers={"Authorization": f"Bearer {key}"}, data=data, timeout=40,
                              files={"file": (f"voice.{ext}", audio, ctype)})
        except requests.RequestException:
            return jsonify(error="Could not reach Groq for speech recognition."), 502
        if r.status_code == 200:
            bump("voice_requests")
            return jsonify(text=(r.json().get("text") or "").strip())
        detail = groq_message(r)
        log.warning("Groq STT %s on %s: %s", r.status_code, model, detail)
        if r.status_code == 401:
            return jsonify(error="Groq rejected the API key."), 502
        if r.status_code == 429:
            return jsonify(error="Groq rate limit reached. Wait a moment and retry."), 502
        error = f"Speech recognition error {r.status_code}: {detail or 'unknown'}"
        if r.status_code not in (400, 403, 404):
            break
    return jsonify(error=error), 502


# ───────────── code execution ─────────────
@app.route("/api/run/status")
def run_status():
    return jsonify(enabled=bool(sandbox.backend()), backend=sandbox.backend(), languages=sandbox.languages(),
                   timeout=sandbox.RUN_TIMEOUT)


def _norm(s: str) -> str:
    return "\n".join(l.rstrip() for l in s.replace("\r\n", "\n").split("\n")).strip()


def _payload():
    d = request.get_json(silent=True) or {}
    lang, code = d.get("language"), d.get("code")
    if not isinstance(lang, str) or not isinstance(code, str) or not code.strip():
        return None, (jsonify(error="Send some code to run."), 400)
    return d, None


@app.route("/api/run", methods=["POST"])
def run_code():
    d, err = _payload()
    if err:
        return err
    msg = allowed("run")
    if msg:
        return jsonify(error=msg), 429
    try:
        res = sandbox.execute(d["language"], d["code"], str(d.get("stdin", "")))
    except sandbox.SandboxError as e:
        return jsonify(error=str(e)), 503
    bump("runs")
    return jsonify(res)


@app.route("/api/test", methods=["POST"])
def run_tests():
    d, err = _payload()
    if err:
        return err
    tests = d.get("tests")
    if not isinstance(tests, list) or not 1 <= len(tests) <= 8 or not all(isinstance(t, dict) for t in tests):
        return jsonify(error="Add between 1 and 8 test cases."), 400
    msg = allowed("run", len(tests))
    if msg:
        return jsonify(error=msg), 429
    results, passed = [], 0
    try:
        for i, t in enumerate(tests, 1):
            exp = str(t.get("expected", ""))
            r = sandbox.execute(d["language"], d["code"], str(t.get("input", "")))
            if r["status"] == "compile_error":          # no point running the rest
                return jsonify(compile_error=r, passed=0, total=len(tests), results=[])
            ok = r["status"] == "ok" and _norm(r["stdout"]) == _norm(exp)
            passed += ok
            results.append(dict(index=i, passed=ok, status=r["status"], expected=exp, stdout=r["stdout"],
                                stderr=r["stderr"], time_ms=r.get("time_ms")))
    except sandbox.SandboxError as e:
        return jsonify(error=str(e)), 503
    bump("test_runs")
    return jsonify(passed=passed, total=len(tests), results=results)


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)),
            debug=(not PRODUCTION and os.getenv("FLASK_DEBUG") == "1"))
