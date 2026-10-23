"""Small helpers shared by the AI-heavy blueprints: API errors and NDJSON progress streaming."""
import json
import logging

from flask import Response, stream_with_context

log = logging.getLogger("codephantom")


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def ndjson(gen_fn):
    """Run a generator of event dicts as a streamed NDJSON response. Errors become a final {"error": ...} event."""
    def gen():
        try:
            for ev in gen_fn():
                yield json.dumps(ev) + "\n"
        except ApiError as e:
            yield json.dumps({"error": str(e)}) + "\n"
        except Exception as e:  # noqa: BLE001
            log.exception("stream failed")
            yield json.dumps({"error": f"Something went wrong ({type(e).__name__}). Try again."}) + "\n"
    return Response(stream_with_context(gen()), mimetype="application/x-ndjson", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


def ev(claim, status, items=None, source=""):
    """One evidence record. status: verified (executed/observed) | static (code analysis) | ai (AI prediction) | uncertain."""
    return {"claim": claim, "status": status, "source": source, "items": items or []}


def item(kind, ref, text):
    return {"type": kind, "ref": str(ref), "text": str(text)[:400]}
