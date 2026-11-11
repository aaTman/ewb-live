#!/usr/bin/env python
"""Summarise the VirtualiZarr metadata survey (survey_vz_*.jsonl) as markdown.

Usage: python scripts/mlwp_survey_report.py [file.jsonl ...]

For each model: coverage, per-variable dtype/shape/chunk layout with *era* detection
(contiguous date ranges sharing one layout signature), codecs, coord grids, and the
list of files that failed to parse (corrupt upstream objects).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np


def load(paths):
    rows, errs = [], []
    for p in paths:
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("error"):
                errs.append(r)
            else:
                rows.append(r)
    return rows, errs


def init_of(url: str) -> str:
    import re

    m = re.search(r"_(\d{10})_f\d{3}_f\d{3}_\d{2}\.nc$", url)
    return m.group(1) if m else "?"


def var_sig(v):
    return (tuple(v.get("dims", [])), str(v.get("dtype")),
            tuple(v.get("chunk_shape") or []),
            json.dumps(v.get("codecs"), sort_keys=True, default=str))


def layout_sig(r):
    return tuple(sorted((k, var_sig(v)) for k, v in r["variables"].items()
                        if not v.get("inline")))


def main() -> None:
    paths = sys.argv[1:] or ["data/cache/mlwp/survey_vz_120d.jsonl"]
    rows, errs = load(paths)
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)

    print(f"# Survey: {len(rows)} files parsed, {len(errs)} parse errors "
          f"({', '.join(paths)})\n")
    for model in sorted(by_model):
        rs = sorted(by_model[model], key=lambda r: r["url"])
        inits = [init_of(r["url"]) for r in rs]
        print(f"## {model} — {len(rs)} files, {inits[0][:8]}..{inits[-1][:8]}")
        hours = sorted({i[8:10] for i in inits if len(i) >= 10})
        print(f"- init hours present: {hours}")

        # eras: contiguous runs of one layout signature
        eras = []
        for r in rs:
            s = layout_sig(r)
            if eras and eras[-1][0] == s:
                eras[-1][1].append(r)
            else:
                eras.append((s, [r]))
        print(f"- layout eras: {len(eras)}")
        for s, chunk in eras:
            span = f"{init_of(chunk[0]['url'])[:8]}..{init_of(chunk[-1]['url'])[:8]}"
            print(f"  - {len(chunk)} files, {span}")
        if len(eras) > 1:
            # show what changed between consecutive eras
            for (s0, c0), (s1, c1) in zip(eras, eras[1:]):
                d0, d1 = dict(s0), dict(s1)
                added = sorted(set(d1) - set(d0))
                removed = sorted(set(d0) - set(d1))
                changed = sorted(k for k in set(d0) & set(d1) if d0[k] != d1[k])
                print(f"    at {init_of(c1[0]['url'])[:8]}: +{added or '-'} "
                      f"-{removed or '-'} changed_chunk_or_codec={changed or '-'}")

        # variable table from the newest file of the newest era
        last = eras[-1][1][-1]
        print("- variables (newest file):")
        for name, v in last["variables"].items():
            if v.get("inline"):
                continue
            codec = "|".join(c[0] for c in v.get("codecs", []))
            print(f"  - {name}: dims={v['dims']} shape={v['shape']} "
                  f"dtype={str(v['dtype']).split('(')[0]} chunk={v.get('chunk_shape')} "
                  f"codecs={codec} nchunks={v.get('nchunks_total')} "
                  f"refs={v.get('manifest_entries')}")
        lv = last["variables"].get("level")
        if lv and lv.get("inline"):
            pass  # values printed in coords section if present
        print()

    if errs:
        print("## Parse errors (unusable upstream files)")
        for e in errs:
            print(f"- {e['url'].split('noaa-oar-mlwp-data/', 1)[-1]}: "
                  f"{e['error'][:110]}")


if __name__ == "__main__":
    main()
