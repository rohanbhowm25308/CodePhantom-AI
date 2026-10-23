# CODEPHANTOM AI
Flask + Groq coding assistant on the Sharingan scroll background. Streamed AI answers, Monaco editor, sandboxed code execution, test runner.

## Run locally
    python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    # put your key in .env  ->  GROQ_API_KEY=...
    python app.py                                        # http://127.0.0.1:5000

## Running code (optional but recommended)
Code never runs in Flask. Choose one:
1. **Docker** (auto-detected): install Docker, start it, run `./pull_images.sh` once (Windows: run the `docker pull` lines inside).
   Each run = throw-away container: no network, 512 MB RAM, 1 CPU, 128 processes, read-only root, no capabilities, non-root user, 5 s limit.
   Java programs must use `public class Main`.
2. **Judge0**: set `JUDGE0_URL` (+ `JUDGE0_TOKEN`) in `.env` (adds TypeScript, C#, Kotlin, Swift, R, SQL).
No Docker/Judge0 -> Run buttons are disabled with a clear message; AI features still work.
AI "expected output" is never executed output; the **Real output** panel and test results come only from the sandbox.

## Deploy (Render / any host)
Build `pip install -r requirements.txt` · Start `gunicorn -w 2 --threads 4 -t 90 app:app`
Set: `APP_ENV=production`, `GROQ_API_KEY`, `REDIS_URL` (shared rate limits), `TRUST_PROXY=1`, `METRICS_TOKEN`.
Render's web service cannot run Docker-in-Docker: use a hosted Judge0 for execution there.
Monitoring: `GET /healthz` (uptime), `GET /api/metrics` with `Authorization: Bearer <METRICS_TOKEN>`; every API call is logged.

## What is inside (v4)
AI modes (17): solve, fix, explain, dry run, complexity, optimize, refactor, convert, security, tests, comments & docs, review & commit,
project starter, project review (attach files), learn, mock interview, ask. Voice input (Groq Whisper). Streamed answers.
Code & Run: Monaco editor, sandboxed run, stdin, test cases, AI-suggested tests, real **Trace** (Python), Diff / Apply fix /
Regression re-run / Benchmark / Validate conversion on AI code, error-line chips, snippets, full-screen editor, dependency check (OSV.dev).
Practice: DSA problems whose expected outputs come from really running a reference solution, hints, solution reveal, daily challenge,
interview pressure timer, progress + weak-topic dashboard. Command palette (Ctrl+K), split view, light/dark, reduced motion, export report.
Optional accounts (SQLite) for a cloud snippet library; autosave + session recovery work without an account.

## Production notes
- Set `SECRET_KEY`, `APP_ENV=production`, `REDIS_URL`, `TRUST_PROXY=1`. SQLite on Render's free tier is wiped on every deploy: attach a disk and set `DATABASE_PATH`.
- Practice and Trace need code execution (Docker, Judge0 or the local dev runner); they are disabled on hosts without it.

## v5 — the 12 standout features (Projects + Labs tabs, Evidence drawer)
**Projects tab** (upload a ZIP, import a public GitHub repo, or start from the editor; set `GITHUB_TOKEN` to raise GitHub's rate limit):
- **Time Machine** — versions are saved automatically; compare any two side by side; record test inputs; *Find when it broke* replays them on every version (real runs); the AI analyzes the diff and its fix is verified by re-running the tests.
- **Codebase X-Ray** — file/function/class map, import graph, endpoints, "where is X implemented" search, grounded Q&A with sources, duplicate logic and potentially unused code.
- **Mission Mode** — plan → per-step proposal with diffs → you approve → applied as a new version → recorded tests re-run → repair or roll back; progress + "what remains" report.
- **Change Impact Radar** — callers, dependents, endpoints, tests, breaking signature changes (Python) and a risk score where every factor shows its evidence.
- **Memory** — architecture / conventions / dependencies / fixes you approve; only approved entries reach the AI.
**Labs tab**: Proof Mode, Review Tribunal (3 reviewers, disagreements surfaced), Spec → Test, Merge Resolver, Security Attack Simulator (rule scan, harmless demos, localhost-only probe — disabled when `APP_ENV=production`).
**Code tab**: Live Trace now draws arrays with pointers, 2-D grids, linked lists, trees, graphs, a growing recursion call tree, breakpoints and step into / over / out.
**Evidence drawer** (button in the tab bar): every result is tagged Verified (really executed/observed), Static, AI prediction or Uncertain.
Multi-file projects run in Docker or the local dev runner (Python, JavaScript, Ruby, PHP, Bash); compiled languages run the entry file alone.
