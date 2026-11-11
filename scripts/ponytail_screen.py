#!/usr/bin/env python3
"""Print the visible screen of an Orca terminal as plain text (debug helper)."""
import json, subprocess, sys
h = sys.argv[1]
out = subprocess.run(["orca", "terminal", "read", "--terminal", h, "--json"],
                     capture_output=True, text=True).stdout
d = json.loads(out)

def lines_of(obj):
    if isinstance(obj, dict):
        if "lines" in obj and isinstance(obj["lines"], list):
            return obj["lines"]
        for v in obj.values():
            r = lines_of(v)
            if r: return r
    return None

def cell_text(c):
    if isinstance(c, str): return c
    if isinstance(c, dict):
        for k in ("text", "char", "ch", "v"):
            if k in c and isinstance(c[k], str): return c[k]
    return ""

r = d.get("result", {})
term = r.get("terminal", r)
tail = term.get("tail")
if tail:
    print("\n".join(t.rstrip() for t in tail))
    sys.exit(0)
ls = lines_of(d)
if ls is None:
    print(json.dumps(d)[:2000]); sys.exit(0)
for ln in ls:
    if isinstance(ln, str): print(ln); continue
    cells = ln.get("cells") or ln.get("c") or []
    print("".join(cell_text(c) for c in cells).rstrip())
