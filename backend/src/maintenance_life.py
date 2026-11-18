"""Mould maintenance life from cumulative trial hours between completed inspections.

The module is deliberately dependency-free so the survival fit can be exercised
without the web/database stack.  A two-parameter Weibull model is fitted by
maximum likelihood to the trial hours accumulated between adjacent completed
maintenance inspections.  The still-open cycle is right-censored at the
assessment cut-off.  The fitted survival function is then conditioned on the
load already accumulated since the last completed inspection to answer:

    P(reach the maintenance point within the next N trial hours | not yet there)
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

MIN_EVENTS = 4
MIN_INTERVALS = 5
MAX_CONTINUED_HOURS = 10000.0
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fingerprint(executions: list[dict], completions: list[dict]) -> str:
    """Stable signature of exactly the records that an assessment saw.

    Business timestamps alone are not enough: a late back-filled actual may
    carry an older ``ended_at``.  The snapshot is therefore keyed by record
    identity as well as its values, so any insert after the cut-off changes the
    fingerprint and forces a fresh assessment before approval.
    """
    payload = {
        "executions": sorted(
            [[str(e["id"]), str(e["ended_at"]), f"{float(e['hours']):.6f}"] for e in executions]
        ),
        "completions": sorted(
            [[str(c["window_id"]), str(c["completed_at"])] for c in completions]
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_intervals(snapshot: dict) -> tuple[list[dict], float]:
    """Attribute completed trial hours to inspection cycles.

    An execution belongs to the cycle in which it ended.  The first observed
    cycle starts at the earliest visible execution; each later cycle starts at
    the previous completion.  The final open cycle is censored at the cut-off.
    """
    executions = [
        {
            "id": e.get("id"),
            "time": _parse(e["ended_at"]),
            "hours": float(e["hours"]),
            "ended_at": e["ended_at"],
        }
        for e in snapshot.get("executions", [])
        if e.get("ended_at") and e.get("hours") is not None
    ]
    executions.sort(key=lambda e: (e["time"], str(e["id"])))
    completions = [
        {
            "window_id": c.get("window_id"),
            "time": _parse(c["completed_at"]),
            "completed_at": c["completed_at"],
        }
        for c in snapshot.get("completions", [])
        if c.get("completed_at")
    ]
    completions.sort(key=lambda c: (c["time"], str(c["window_id"])))

    intervals: list[dict] = []
    # Only intervals bounded by two completed inspections count as observed
    # maintenance cycles ("相邻完检间").  Data before the first completion has
    # no known starting inspection and is therefore not claimed as evidence.
    for index in range(1, len(completions)):
        previous = completions[index - 1]["time"]
        end = completions[index]["time"]
        cycle = [e for e in executions if previous < e["time"] <= end]
        hours = round(sum(e["hours"] for e in cycle), 4)
        intervals.append({
            "kind": "event",
            "window_id": completions[index]["window_id"],
            "from": completions[index - 1]["completed_at"],
            "to": completions[index]["completed_at"],
            "hours": hours,
            "usable": hours > 0,
        })

    last = completions[-1]["time"] if completions else None
    open_cycle = [e for e in executions if last is None or e["time"] > last]
    current = round(sum(e["hours"] for e in open_cycle), 4)
    intervals.append({
        "kind": "censored",
        "window_id": None,
        "from": completions[-1]["completed_at"] if completions else (
            open_cycle[0]["ended_at"] if open_cycle else None
        ),
        "to": snapshot.get("cutoff_at"),
        "hours": current,
        "usable": current > 0,
    })
    return intervals, current


def _weibull_fit(times: list[float], events: list[int]) -> tuple[float, float]:
    """Profile-likelihood Weibull MLE for right-censored observations.

    For a fixed shape ``k`` the scale has the closed form
    ``lambda = (sum(t_i**k) / d) ** (1/k)`` where ``d`` is the event count.
    The profile log-likelihood is unimodal in practice for a Weibull, so a
    deterministic golden-section search is enough and avoids extra packages.
    """
    d = sum(events)
    if d <= 0:
        raise ValueError("no observed maintenance cycle")
    positive = [t for t in times if t > 0]
    if not positive:
        raise ValueError("no positive maintenance cycle duration")

    def profile(k: float) -> float:
        powered = [t ** k for t in positive]
        total = sum(powered)
        if total <= 0:
            return float("-inf")
        ll = sum(
            math.log(k) + (k - 1.0) * math.log(t)
            for t, observed in zip(times, events)
            if observed and t > 0
        )
        ll -= d * math.log(total / d)
        ll -= d
        return ll

    low, high = 0.05, 50.0
    x1 = high - _GOLDEN * (high - low)
    x2 = low + _GOLDEN * (high - low)
    f1, f2 = profile(x1), profile(x2)
    for _ in range(200):
        if f1 < f2:
            low = x1
            x1, f1 = x2, f2
            x2 = low + _GOLDEN * (high - low)
            f2 = profile(x2)
        else:
            high = x2
            x2, f2 = x1, f1
            x1 = high - _GOLDEN * (high - low)
            f1 = profile(x1)
        if high - low < 1e-10:
            break
    shape = (low + high) / 2.0
    scale = (sum(t ** shape for t in positive) / d) ** (1.0 / shape)
    return shape, scale


def _survival(hours: float, shape: float, scale: float) -> float:
    if hours <= 0:
        return 1.0
    return math.exp(-((hours / scale) ** shape))


def assess(snapshot: dict, continued_hours: float) -> dict[str, Any]:
    """Return an assessment payload, including a blocked payload if samples fail."""
    intervals, current = build_intervals(snapshot)
    observed = [i for i in intervals if i["kind"] == "event" and i["hours"] > 0]
    total = len(observed) + 1  # the open, right-censored cycle
    sample = {
        "observed_intervals": len(observed),
        "censored_intervals": 1,
        "total_intervals": total,
        "min_events": MIN_EVENTS,
        "min_total": MIN_INTERVALS,
        "observed_hours": round(sum(i["hours"] for i in observed), 3),
        "censored_hours": current,
    }
    blocking: list[str] = []
    if len(observed) < MIN_EVENTS:
        blocking.append(
            f"完检间期样本不足：仅有 {len(observed)} 个有效完检间期，至少需要 {MIN_EVENTS} 个"
        )
    if total < MIN_INTERVALS:
        blocking.append(
            f"生存样本不足：有效间期合计 {total} 个，至少需要 {MIN_INTERVALS} 个"
        )
    if continued_hours <= 0 or continued_hours > MAX_CONTINUED_HOURS:
        blocking.append(f"继续试模工时须在 0 到 {int(MAX_CONTINUED_HOURS)} 小时之间")
    if blocking:
        return {
            "status": "blocked",
            "method": "Weibull MLE / right-censored",
            "cutoff_at": snapshot.get("cutoff_at"),
            "continued_hours": continued_hours,
            "current_load_hours": current,
            "intervals": intervals,
            "sample": sample,
            "blocking_reasons": blocking,
            "shape": None,
            "scale_hours": None,
            "probability": None,
        }

    times = [float(i["hours"]) for i in observed] + [float(current)]
    events = [1] * len(observed) + [0]
    shape, scale = _weibull_fit(times, events)
    horizon = current + continued_hours
    survival_now = _survival(current, shape, scale)
    survival_after = _survival(horizon, shape, scale)
    probability = max(0.0, min(1.0, 1.0 - survival_after / survival_now))
    return {
        "status": "ready",
        "method": "Weibull MLE / right-censored",
        "cutoff_at": snapshot.get("cutoff_at"),
        "continued_hours": round(continued_hours, 4),
        "current_load_hours": current,
        "horizon_load_hours": round(horizon, 4),
        "shape": round(shape, 6),
        "scale_hours": round(scale, 4),
        "survival_now": round(survival_now, 6),
        "survival_after": round(survival_after, 6),
        "probability": round(probability, 4),
        "intervals": intervals,
        "sample": sample,
        "blocking_reasons": [],
        "note": (
            "以相邻完检间累计试模工时拟合 Weibull；未完检的当前间期按右删失处理。"
            "概率为在当前累计负荷之上继续试模指定工时后到达保养点的条件概率。"
        ),
    }
