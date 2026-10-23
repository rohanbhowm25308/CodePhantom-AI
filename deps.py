"""Dependency risk review: looks pinned packages up in the public OSV.dev vulnerability database."""
import json
import re

import requests

OSV = "https://api.osv.dev/v1/querybatch"
MAX_PKGS = 60


def parse(text: str):
    """Returns (ecosystem, [(name, version|None)]) from a requirements.txt or package.json."""
    text = (text or "").strip()
    pkgs = []
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("That is not valid package.json.")
        for sect in ("dependencies", "devDependencies"):
            for name, ver in (data.get(sect) or {}).items():
                m = re.match(r"^[\^~]?(\d+\.\d+\.\d+[\w.\-+]*)$", str(ver).strip())
                pkgs.append((name, m.group(1) if m else None))
        return "npm", pkgs[:MAX_PKGS]
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[.*?\])?\s*==\s*([A-Za-z0-9_.\-+]+)", line)
        if m:
            pkgs.append((m.group(1), m.group(2)))
        else:
            pkgs.append((re.split(r"[<>=!~\s;\[]", line)[0], None))
    return "PyPI", pkgs[:MAX_PKGS]


def check(text: str):
    eco, pkgs = parse(text)
    if not pkgs:
        raise ValueError("No packages found. Paste a requirements.txt or package.json.")
    pinned = [(n, v) for n, v in pkgs if v]
    results = []
    if pinned:
        body = {"queries": [{"package": {"name": n, "ecosystem": eco}, "version": v} for n, v in pinned]}
        try:
            r = requests.post(OSV, json=body, timeout=20)
            r.raise_for_status()
            found = r.json().get("results", [])
        except (requests.RequestException, ValueError):
            raise RuntimeError("Could not reach the OSV.dev vulnerability database.")
        for (n, v), res in zip(pinned, found):
            ids = [x["id"] for x in (res.get("vulns") or [])][:10]
            results.append({"name": n, "version": v, "vulns": ids, "links": [f"https://osv.dev/vulnerability/{i}" for i in ids]})
    unpinned = [n for n, v in pkgs if not v]
    return {"ecosystem": eco, "checked": results, "unpinned": unpinned,
            "note": "Only exactly pinned versions (name==1.2.3) can be checked. A clean result does not prove a package is safe."}
