"""Real execution tracing for Python: runs the user's code under sys.settrace inside the normal sandbox and returns
the actual line-by-line steps (line, local variables, call stack). Nothing here is AI-guessed."""
import json

SENT = "\x1eCPTRACE\x1e"
MAX_STEPS = 300

HARNESS = r'''
import sys, json, types, linecache
_SRC = __SRC__
_SENT = "\x1eCPTRACE\x1e"
_STEPS, _STACK, _MAX, _TRUNC, _ERR = [], [], __MAX__, False, None
linecache.cache["<user>"] = (len(_SRC), None, _SRC.splitlines(True), "<user>")
def _fmt(v):
    try:
        s = repr(v)
    except Exception:
        s = "<unprintable>"
    return s if len(s) <= 40 else s[:37] + "..."
def _enc(v, d=0, seen=None):
    if seen is None:
        seen = set()
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else {"t": "repr", "v": repr(v)}
    if isinstance(v, str):
        return v if len(v) <= 60 else v[:57] + "..."
    if isinstance(v, (list, tuple, set, frozenset)):
        if d >= 3:
            return {"t": "repr", "v": _fmt(v)}
        items = sorted(v, key=repr)[:12] if isinstance(v, (set, frozenset)) else list(v)[:12]
        return {"t": "list", "k": type(v).__name__, "n": len(v), "v": [_enc(x, d + 1, seen) for x in items]}
    if isinstance(v, dict):
        if d >= 3:
            return {"t": "repr", "v": _fmt(v)}
        return {"t": "dict", "n": len(v), "v": [[_enc(k, d + 1, seen), _enc(x, d + 1, seen)] for k, x in list(v.items())[:10]]}
    if hasattr(v, "__dict__") and not isinstance(v, (types.FunctionType, type, types.ModuleType)):
        oid = id(v) % 100000
        if oid in seen or d >= 5:
            return {"t": "ref", "id": oid, "c": type(v).__name__}
        seen.add(oid)
        return {"t": "obj", "c": type(v).__name__, "id": oid, "a": {k: _enc(x, d + 1, seen) for k, x in list(vars(v).items())[:8] if not k.startswith("__")}}
    return {"t": "repr", "v": _fmt(v)}
def _vars(f):
    out = {}
    for k, v in list(f.f_locals.items()):
        if k.startswith("__") or isinstance(v, (types.FunctionType, types.ModuleType, type, types.BuiltinFunctionType)):
            continue
        out[k] = _enc(v)
        if len(out) >= 14:
            break
    return out
_SIZE = 0
def _step(kind, f, **extra):
    global _SIZE, _TRUNC
    d = {"e": kind, "l": f.f_lineno, "v": _vars(f), "s": _STACK[-8:], "d": len(_STACK)}
    d.update(extra)
    _SIZE += len(json.dumps(d))
    if _SIZE > 420000:
        _TRUNC = True
        sys.settrace(None)
        return
    _STEPS.append(d)
def _tr(frame, event, arg):
    global _TRUNC
    if frame.f_code.co_filename != "<user>":
        return None
    if len(_STEPS) >= _MAX:
        _TRUNC = True
        sys.settrace(None)
        return None
    if event == "call":
        _STACK.append("main" if frame.f_code.co_name == "<module>" else frame.f_code.co_name)
        if frame.f_code.co_name != "<module>":      # the module "call" event reports line 0 on Python 3.11+
            _step("call", frame)
    elif event == "line":
        _step("line", frame)
    elif event == "return":
        _step("return", frame, r=_fmt(arg))
        if _STACK:
            _STACK.pop()
    elif event == "exception":
        _step("error", frame, r=_fmt(arg[1]))
    return _tr
_ns = {"__name__": "__main__", "__builtins__": __builtins__}
try:
    _code = compile(_SRC, "<user>", "exec")
    sys.settrace(_tr)
    exec(_code, _ns)
except SystemExit:
    pass
except BaseException as _e:
    _ERR = type(_e).__name__ + ": " + str(_e)
    import traceback
    traceback.print_exc()
finally:
    sys.settrace(None)
sys.stdout.flush()
sys.stdout.write("\n" + _SENT + json.dumps({"steps": _STEPS, "truncated": _TRUNC, "error": _ERR}))
'''


def build(code: str) -> str:
    return HARNESS.replace("__SRC__", repr(code)).replace("__MAX__", str(MAX_STEPS))


def parse(stdout: str):
    """Split program output from the trace blob. Returns (user_stdout, trace_dict | None)."""
    if SENT not in stdout:
        return stdout, None
    out, _, blob = stdout.rpartition(SENT)
    if out.endswith("\n"):
        out = out[:-1]
    try:
        return out, json.loads(blob)
    except ValueError:
        return out, None
