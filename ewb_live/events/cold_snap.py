"""Cold-snap events: the freeze rule WITHOUT the ``<= 0 degC`` requirement.

Definition (product owner, 2026-11-16): identical to freeze — 2 m temperature
below the local p15 of the OWB ERA5 climatology at **all four** 6-hourly steps
of a UTC day, over land, same footprint/core tracker, same area and duration
floors — except the temperature does not have to reach 0 degC. So detection is
literally ``scan_temperature_events(..., event_type="cold_snap")``: the freeze
cap (``temperature.FREEZE_MAX_C``) is the only difference, and it is expressed
as a per-type entry in ``temperature.MAX_C_BY_TYPE`` (``None`` for cold_snap)
rather than a copy of the scanner. The published raw-exceedance lane needs no
new layer either: the p15 "freeze" bit *is* the cold-snap mask, which is why
``qualified_at`` reads frame layer 1 for cold snaps too (SPEC §2).

How cold snaps relate to freezes
--------------------------------
The freeze mask is a subset of the cold-snap mask, so every freeze spell is also
a cold snap and the same spell would otherwise be listed (and scored) twice.
Two options were on the table:

(a) publish both lanes side by side and flag the overlap in ``details``;
(b) dedupe the duplicate in the curated selection.

**Chosen: (b) for the published selection, with (a)'s flag kept in the raw
catalogue.** Rationale: the owner's curation rule (SPEC §2, select.py,
2026-11-06) forbids two events overlapping in BOTH time and space in
``events_selected.json`` — the file the map, the event table, the scorecard and
scoring read — and a side-by-side duplicate would also double the scorecard
weight of one spell (and ~15 extra event-scoring jobs per weekly run). So:

* ``events.json`` (the full detector catalogue, no curation) lists cold snaps
  next to freezes, and each cold snap says how much of it was actually sub-zero
  — ``details.subzero_cell_fraction`` / ``details.contains_subzero`` — which is
  option (a)'s overlap flag, measured at mask resolution instead of by
  comparing two event boxes.
* the curated selection drops a cold snap that overlaps a freeze in time+space
  (:func:`subsume_freeze_overlaps`, called from ``detect_events`` before
  curation). The freeze wins the contested box because it is the *more
  specific* physical claim ("it actually froze"); cold snaps that stay above
  0 degC — the part the freeze lane structurally cannot describe — are
  published unchanged. Every drop lands in ``curation.json`` under
  ``reason: "freeze-subsumed"`` so the decision is auditable and reversible.

Partial overlaps with any *other* type keep going through the existing
cross-type rule (``select._resolve_conflicts``).

EWB side (installed 1.1.1): it ships no cold-snap event type — its ``events/``
package has AR, severe convection and tropical cyclone only — and ``event_type``
is a free-form string used for nothing but matching a case to its
``EvaluationObject`` (``cases.py``). So ``"cold_snap"`` passes through EWB
untouched and the event is scored with freeze's EWB ingredients
(``defaults.era5_freeze_target`` + MinMAE / RMSE / DurationMeanError at the p15
threshold): the quantities are identical, only the event set differs.
"""

from __future__ import annotations

from typing import Any

import numpy as np

COLD_SNAP = "cold_snap"

# the freeze cap for this type: None == no temperature ceiling at all
# (owner 2026-11-16: cold snap = freeze minus the <=0C requirement)
MAX_C: float | None = None


def subzero_flags(active: np.ndarray, dmax_c: np.ndarray) -> dict[str, Any]:
    """Cold-snap-only detail fields: how much of the spell was sub-zero.

    ``active`` is the event's daily valid-point mask inside its box
    (``(days, lat, lon)`` bool) and ``dmax_c`` the daily-MAXIMUM 2 m
    temperature (degC) on the same slice, so ``dmax_c <= 0`` marks a cell-day
    that met the freeze rule's temperature cap at all four steps. Returns
    ``{}`` when either array is empty (no truth mirror for the window), so the
    caller can splice the result into ``details`` unconditionally.
    """
    if active is None or dmax_c is None or not active.size:
        return {}
    cells = active.reshape(-1)
    if not cells.any():
        return {}
    sub = (np.asarray(dmax_c, np.float32) <= 0.0).reshape(-1)[cells]
    frac = float(sub.mean()) if sub.size else 0.0
    return {
        "subzero_cell_fraction": round(frac, 4),
        "contains_subzero": bool(sub.any()) if sub.size else None,
    }


def subsume_freeze_overlaps(events: list, decisions: list[dict]
                            ) -> list:
    """Drop cold snaps that a freeze event already claims (see module docstring).

    ``events`` is the raw multi-type scan list; ``decisions`` (the curation log,
    ``select.LAST_DECISIONS``) receives one ``action: "drop"`` entry per
    subsumed cold snap. Freeze events are returned untouched, and the input
    order is preserved, so this only ever removes cold-snap list members.
    """
    from .select import _overlaps   # same time+space predicate as curation

    freezes = [e for e in events if e.event_type == "freeze"]
    if not freezes:
        return events
    out: list = []
    for e in events:
        if e.event_type == COLD_SNAP:
            hit = next((f for f in freezes if _overlaps(f, e)), None)
            if hit is not None:
                decisions.append({
                    "action": "drop", "reason": "freeze-subsumed",
                    "event_type": COLD_SNAP, "id": e.id,
                    "kept": hit.id, "kept_type": "freeze",
                    "note": "cold snap overlaps a freeze in time+space; the "
                            "freeze (<=0C at all four steps) is the more "
                            "specific claim and owns the box",
                })
                continue
        out.append(e)
    return out
