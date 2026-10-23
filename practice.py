"""DSA practice: AI writes the problem AND a reference solution; the reference is executed in the sandbox so the
expected outputs are REAL program output, never an AI guess. Hidden tests come from the same verified runs."""
import datetime
import json
import os
import uuid

from flask import Blueprint, jsonify, request

import accounts
import sandbox
from groq_client import complete, extract_json

bp = Blueprint("practice", __name__)
allowed = bump = None        # injected by app.py
TOPICS = ["Arrays", "Strings", "Hashing", "Linked lists", "Stacks and queues", "Trees", "Graphs", "Dynamic programming",
          "Sorting and searching", "Recursion", "Math", "Two pointers"]
DIFFS = ["Easy", "Medium", "Hard"]

SYSTEM = (
    "You write programming practice problems that read from standard input and print to standard output. "
    "Reply with ONE JSON object only, with keys: title (string), statement (markdown, clear and self-contained), "
    "input_format (string), output_format (string), tests (array of 6 objects {\"input\": \"<exact stdin text>\"}; the first two are small "
    "examples, the rest include edge cases and a larger case), hints (array of exactly 3 strings, progressively more revealing, "
    "the last still not the full code), solution_python (a complete correct Python 3 program reading stdin and printing the answer), "
    "explanation (string), time_complexity (string), space_complexity (string). "
    "Every input must have exactly ONE correct output (no 'any valid answer' problems, no randomness, no floating-point output). "
    "Keep inputs small enough to run in under one second."
)


def make_problem(key, topic, diff):
    """Returns (problem | None, error)."""
    last = "Could not create a verified problem. Try again."
    for _ in range(2):
        txt, err = complete(key, SYSTEM, f"Topic: {topic}. Difficulty: {diff}. Create a fresh, interesting problem.", max_tokens=3500, json_mode=True)
        if err:
            return None, err
        d = extract_json(txt)
        try:
            tests = [str(t["input"]) for t in d["tests"]][:6]
            hints = [str(h) for h in d["hints"]][:3]
            sol = str(d["solution_python"])
            title, statement = str(d["title"])[:120], str(d["statement"])[:4000]
            assert tests and len(hints) == 3 and sol.strip() and title and statement
        except (TypeError, KeyError, AssertionError):
            last = "The AI returned an incomplete problem. Try again."
            continue
        verified, ok = [], True
        for t in tests:
            r = sandbox.execute("python", sol, t)
            if r["status"] != "ok" or not r["stdout"].strip():
                ok = False
                break
            verified.append({"input": t, "expected": r["stdout"].strip()})
        if not ok:
            last = "The reference solution failed verification. Try again."
            continue
        return {"id": uuid.uuid4().hex[:10], "title": title, "statement": statement, "topic": topic, "difficulty": diff,
                "input_format": str(d.get("input_format", ""))[:500], "output_format": str(d.get("output_format", ""))[:500],
                "tests": verified, "hints": hints, "solution": sol, "explanation": str(d.get("explanation", ""))[:2500],
                "time_complexity": str(d.get("time_complexity", ""))[:80], "space_complexity": str(d.get("space_complexity", ""))[:80],
                "verified": True}, None
    return None, last


def _guard():
    if not sandbox.backend() or "python" not in sandbox.languages():
        return jsonify(error="Practice needs code execution enabled, so solutions can be verified (Docker, Judge0 or the local runner)."), 503
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return jsonify(error="Server has no GROQ_API_KEY."), 503
    msg = allowed("ai")
    if msg:
        return jsonify(error=msg), 429
    return None


@bp.route("/api/practice/new", methods=["POST"])
def new_problem():
    bad = _guard()
    if bad:
        return bad
    d = request.get_json(silent=True) or {}
    topic, diff = d.get("topic"), d.get("difficulty")
    if topic not in TOPICS or diff not in DIFFS:
        return jsonify(error="Pick a topic and difficulty."), 400
    p, err = make_problem(os.environ["GROQ_API_KEY"], topic, diff)
    if err:
        return jsonify(error=err), 502
    bump("practice_problems")
    return jsonify(problem=p)


@bp.route("/api/practice/daily")
def daily():
    bad = _guard()
    if bad:
        return bad
    today = datetime.date.today()
    day = today.isoformat()
    with accounts.connect() as c:
        row = c.execute("SELECT data FROM daily WHERE day=?", (day,)).fetchone()
        if row:
            return jsonify(problem=json.loads(row["data"]))
    n = today.toordinal()
    p, err = make_problem(os.environ["GROQ_API_KEY"], TOPICS[n % len(TOPICS)], DIFFS[(n // len(TOPICS)) % 2])   # Easy / Medium rotation
    if err:
        return jsonify(error=err), 502
    p["id"] = "daily-" + day
    p["daily"] = day
    with accounts.connect() as c:
        c.execute("INSERT OR IGNORE INTO daily(day,data) VALUES(?,?)", (day, json.dumps(p)))
        row = c.execute("SELECT data FROM daily WHERE day=?", (day,)).fetchone()
    bump("practice_daily")
    return jsonify(problem=json.loads(row["data"]))
