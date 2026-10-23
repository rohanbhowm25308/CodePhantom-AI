"""Optional accounts + cloud snippet library (SQLite). Passwords are hashed; sessions are signed cookies."""
import os
import re
import sqlite3
import time

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "codephantom.db"))
bp = Blueprint("accounts", __name__)
take = None            # injected by app.py (shared rate limiter)
MAX_SNIPPETS = 100


def connect():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS snippets(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT, language TEXT,
            project TEXT, code TEXT, favorite INTEGER DEFAULT 0, updated REAL);
        CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, data TEXT);
        """)


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@bp.teardown_app_request
def _close(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


@bp.before_request
def _csrf():
    # state-changing calls must come from our own JS (custom header can't be sent cross-site without CORS)
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Requested-With") != "codephantom":
        return jsonify(error="Forbidden."), 403


def uid():
    return session.get("uid")


def need_login():
    return None if uid() else (jsonify(error="Sign in to use your snippet library."), 401)


EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[^@\s]{2,24}$")


@bp.route("/api/auth/me")
def me():
    if not uid():
        return jsonify(user=None)
    row = db().execute("SELECT email FROM users WHERE id=?", (uid(),)).fetchone()
    if not row:
        session.clear()
        return jsonify(user=None)
    return jsonify(user={"email": row["email"]})


def _limited():
    return take and not take("auth", request.remote_addr or "?", 10, 60)


@bp.route("/api/auth/register", methods=["POST"])
def register():
    if _limited():
        return jsonify(error="Too many attempts. Wait a minute."), 429
    d = request.get_json(silent=True) or {}
    email, pw = str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    if not EMAIL.match(email):
        return jsonify(error="Enter a valid email address."), 400
    if not 8 <= len(pw) <= 128:
        return jsonify(error="Password must be 8 to 128 characters."), 400
    try:
        cur = db().execute("INSERT INTO users(email,pw,created) VALUES(?,?,?)", (email, generate_password_hash(pw), time.time()))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="That email is already registered."), 409
    session.clear()
    session["uid"] = cur.lastrowid
    return jsonify(user={"email": email})


@bp.route("/api/auth/login", methods=["POST"])
def login():
    if _limited():
        return jsonify(error="Too many attempts. Wait a minute."), 429
    d = request.get_json(silent=True) or {}
    email, pw = str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    row = db().execute("SELECT id,pw FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_password_hash(row["pw"], pw):
        return jsonify(error="Wrong email or password."), 401
    session.clear()
    session["uid"] = row["id"]
    return jsonify(user={"email": email})


@bp.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)


def _row(r):
    return {"id": r["id"], "title": r["title"], "language": r["language"], "project": r["project"], "code": r["code"],
            "favorite": bool(r["favorite"]), "updated": r["updated"]}


@bp.route("/api/snippets")
def snippets_list():
    bad = need_login()
    if bad:
        return bad
    rows = db().execute("SELECT * FROM snippets WHERE user_id=? ORDER BY favorite DESC, updated DESC LIMIT ?", (uid(), MAX_SNIPPETS)).fetchall()
    return jsonify(snippets=[_row(r) for r in rows])


@bp.route("/api/snippets", methods=["POST"])
def snippets_save():
    bad = need_login()
    if bad:
        return bad
    d = request.get_json(silent=True) or {}
    title, lang, project, code = (str(d.get(k, "")) for k in ("title", "language", "project", "code"))
    title, lang, project = title.strip()[:80], lang[:30], project.strip()[:40]
    if not title or not code.strip() or len(code) > 50_000:
        return jsonify(error="A title and code (up to 50,000 characters) are required."), 400
    fav, now = 1 if d.get("favorite") else 0, time.time()
    sid = d.get("id")
    c = db()
    if sid:
        cur = c.execute("UPDATE snippets SET title=?,language=?,project=?,code=?,favorite=?,updated=? WHERE id=? AND user_id=?",
                        (title, lang, project, code, fav, now, sid, uid()))
        if cur.rowcount == 0:
            return jsonify(error="Snippet not found."), 404
    else:
        if c.execute("SELECT COUNT(*) FROM snippets WHERE user_id=?", (uid(),)).fetchone()[0] >= MAX_SNIPPETS:
            return jsonify(error=f"Snippet limit reached ({MAX_SNIPPETS}). Delete some first."), 400
        cur = c.execute("INSERT INTO snippets(user_id,title,language,project,code,favorite,updated) VALUES(?,?,?,?,?,?,?)",
                        (uid(), title, lang, project, code, fav, now))
        sid = cur.lastrowid
    c.commit()
    return jsonify(snippet=_row(c.execute("SELECT * FROM snippets WHERE id=?", (sid,)).fetchone()))


@bp.route("/api/snippets/<int:sid>", methods=["DELETE"])
def snippets_delete(sid):
    bad = need_login()
    if bad:
        return bad
    c = db()
    c.execute("DELETE FROM snippets WHERE id=? AND user_id=?", (sid, uid()))
    c.commit()
    return jsonify(ok=True)
