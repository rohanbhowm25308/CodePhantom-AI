"""Groq helpers shared by the whole backend: model fallback chain, streaming and plain JSON completions."""
import json
import logging
import os
import re

import requests

log = logging.getLogger("codephantom")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq retired llama-3.3-70b-versatile and llama-3.1-8b-instant on 16 Aug 2026. Retired names in .env are ignored.
RETIRED = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192", "llama3-8b-8192", "mixtral-8x7b-32768",
           "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"}
DEFAULT_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]
_cfg = os.getenv("GROQ_MODEL", "").strip()
MODEL_CHAIN = ([_cfg] if _cfg and _cfg not in RETIRED else []) + [m for m in DEFAULT_MODELS if m != _cfg]
MODEL = MODEL_CHAIN[0]


def groq_message(r) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return ""


def _headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _fatal(status, detail):
    if status == 401:
        return "Groq rejected the API key. Check GROQ_API_KEY in .env (no quotes or spaces)."
    if status == 429:
        return "Groq rate limit reached. Wait a moment and retry."
    return None


def open_stream(key: str, system: str, messages: list):
    """Streaming chat. Tries each model in turn. Returns (response, error_message)."""
    err = "Groq returned an error."
    for model in MODEL_CHAIN:
        body = {"model": model, "stream": True, "temperature": 0.2, "max_tokens": 6000,
                "messages": [{"role": "system", "content": system}] + messages}
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        try:
            r = requests.post(GROQ_URL, stream=True, timeout=(8, 60), json=body, headers=_headers(key))
        except requests.Timeout:
            return None, "The AI took too long to respond. Try again."
        except requests.RequestException:
            return None, "Could not reach Groq. Check your internet connection."
        if r.status_code == 200:
            return r, None
        detail = groq_message(r)
        r.close()
        log.warning("Groq %s on model %s: %s", r.status_code, model, detail)
        fatal = _fatal(r.status_code, detail)
        if fatal:
            return None, fatal
        err = f"Groq error {r.status_code}: {detail or 'unknown error'}"
        if r.status_code in (400, 403, 404) and re.search(r"model|decommission|deprecat|not found|access", detail, re.I):
            continue
        break
    return None, err


def complete(key: str, system: str, user: str, max_tokens: int = 3500, json_mode: bool = False):
    """Non-streaming completion. Returns (text, error_message)."""
    err = "Groq returned an error."
    for model in MODEL_CHAIN:
        for jm in ([True, False] if json_mode else [False]):
            body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if jm:
                body["response_format"] = {"type": "json_object"}
            if model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"
            try:
                r = requests.post(GROQ_URL, json=body, headers=_headers(key), timeout=(8, 75))
            except requests.Timeout:
                return None, "The AI took too long to respond. Try again."
            except requests.RequestException:
                return None, "Could not reach Groq. Check your internet connection."
            if r.status_code == 200:
                try:
                    return r.json()["choices"][0]["message"]["content"] or "", None
                except (KeyError, IndexError, ValueError):
                    return None, "Unexpected response from Groq."
            detail = groq_message(r)
            log.warning("Groq %s on model %s (json=%s): %s", r.status_code, model, jm, detail)
            fatal = _fatal(r.status_code, detail)
            if fatal:
                return None, fatal
            err = f"Groq error {r.status_code}: {detail or 'unknown error'}"
            if jm and r.status_code == 400:
                continue                       # this model may not support JSON mode: retry without it
            if r.status_code in (400, 403, 404) and re.search(r"model|decommission|deprecat|not found|access", detail, re.I):
                break                          # next model
            return None, err
    return None, err


def extract_json(text: str):
    """Parse a JSON object out of a model reply (tolerates code fences / chatter)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except ValueError:
                return None
    return None
