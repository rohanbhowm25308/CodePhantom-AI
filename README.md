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
