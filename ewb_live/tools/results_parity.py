"""Parity check: results store vs the published ``data/results`` files (SPEC §6 phase 2).

    python -m ewb_live.tools.results_parity [--results data/results] [--ref main] [--repo PATH]

Compares event ids against ``events_selected.json``, score rows against
``scores.parquet`` (events the store holds), run-level docs and per-event ``viz.json``
byte-for-byte. Exit 0 = identical; 1 = mismatch (problems listed, first 20 printed).
Read-only on both sides.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ewb_live.results_sink import DOCS
from ewb_live.stores import results_icechunk as rs

_KEYS = ["event_id", "model", "metric", "lead_time_hours", "init_time"]


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df[[*_KEYS, "value"]].copy()
    df["init_time"] = pd.to_datetime(df["init_time"]).astype("datetime64[s]")
    df["lead_time_hours"] = df["lead_time_hours"].astype("int64")
    return df.sort_values(_KEYS, kind="stable").reset_index(drop=True)


def compare(results: Path, repo, ref: str = rs.PUBLISHED) -> dict:
    results = Path(results)
    problems: list[str] = []
    selected = [e["id"] for e in json.loads((results / "events_selected.json").read_text())]
    in_store = {e["id"] for e in rs.list_events(repo, ref)}
    if missing := sorted(set(selected) - in_store):
        problems.append(f"{len(missing)} selected events missing from store: {missing[:5]}")
    if extra := sorted(in_store - set(selected)):
        problems.append(f"{len(extra)} store events not in events_selected.json: {extra[:5]}")

    files = _norm(pd.read_parquet(results / "scores.parquet"))
    files = files[files["event_id"].isin(in_store)].reset_index(drop=True)
    store = _norm(rs.read_scores(repo, ref))
    if len(files) != len(store):
        problems.append(f"score rows: files {len(files)} vs store {len(store)}")
    else:
        keys_eq = (files[_KEYS].astype(str).to_numpy() == store[_KEYS].astype(str).to_numpy()).all()
        if not keys_eq:
            problems.append("score row keys differ")
        elif not np.allclose(files["value"], store["value"], equal_nan=True, rtol=0, atol=1e-9):
            problems.append("score values differ")

    for name in DOCS:
        p = results / name
        if not p.is_file():
            continue
        try:
            same = hashlib.sha1(p.read_bytes()).digest() == hashlib.sha1(rs.read_doc(repo, name, ref)).digest()
        except KeyError:
            same = False
        if not same:
            problems.append(f"doc {name} differs or missing in store")
    n_viz = n_bad = 0
    for eid in sorted(in_store):
        p = results / "events" / eid / "viz.json"
        if not p.is_file():
            continue
        n_viz += 1
        try:
            ok = p.read_bytes() == rs.read_blob(repo, eid, "viz.json", ref)
        except KeyError:
            ok = False
        n_bad += not ok
    if n_bad:
        problems.append(f"{n_bad}/{n_viz} viz.json blobs differ or missing in store")
    return {"ok": not problems, "problems": problems, "events": len(in_store),
            "score_rows": len(store), "viz_blobs": n_viz, "ref": ref}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="data/results")
    ap.add_argument("--ref", default=rs.PUBLISHED, help="branch, tag or snapshot id")
    ap.add_argument("--repo", default=None)
    a = ap.parse_args(argv)
    rep = compare(Path(a.results), rs.open_repo(a.repo), a.ref)
    print(json.dumps({**rep, "problems": rep["problems"][:20]}, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
