"""Monthly budget per agent, with an automatic pause.

A budget is a number of US dollars per calendar month for one agent (Hermes profile). The spend is
what Hermes recorded for that profile's sessions since the 1st of the month (the same estimate the
Activity page shows). At 80% the agent is "near its limit"; at 100% it is "over budget" and paused:
- the panel refuses chat and new tasks for it (agent_api._require_ready),
- scripts/escalate.py puts its waiting tasks on hold, like a disabled agent's.
Work that is already running is not cut off. The pause ends by itself on the 1st of the next month,
or when the budget is raised or removed. Nothing in team.yaml or the Hermes profiles is changed.

Sessions whose model Hermes cannot price count as $0 and are reported as "unpriced", so a budget
on such an agent can only be a lower bound.

The budgets live in data/budgets.json (not in git): {"agents": {"<profile>": <usd>}}.
"""
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import activity_api

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STORE = os.path.join(os.environ.get("PANEL_DATA") or os.path.join(ROOT, "data"), "budgets.json")
WARN = 0.8


def _load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as handle:
            agents = json.load(handle).get("agents") or {}
        return {k: float(v) for k, v in agents.items() if isinstance(v, (int, float)) and v > 0}
    except (OSError, ValueError, AttributeError):
        return {}


def _save(agents: dict):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "agents": agents}, handle, indent=1)
    os.replace(tmp, STORE)


def month_start(now: float = None) -> float:
    t = time.localtime(now if now is not None else time.time())
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def state_of(spent: float, budget) -> str:
    if not budget:
        return "none"
    return "over" if spent >= budget else "near" if spent >= WARN * budget else "ok"


def spent_this_month(profile: str, now: float = None) -> dict:
    total = {"cost": 0.0, "unpriced": 0}
    try:
        rows = activity_api._sessions(profile, month_start(now))
    except Exception:
        return total
    for row in rows:
        u = activity_api._usage(row)
        total["cost"] += u["cost"]
        total["unpriced"] += u["cost_unknown"]
    return total


def status(now: float = None) -> list:
    """One row per agent that has a budget, plus the others with their spend (budget None)."""
    budgets, rows = _load(), []
    for profile, info in sorted(activity_api.profiles().items()):
        spent = spent_this_month(profile, now)
        budget = budgets.get(profile)
        rows.append({"profile": profile, "role": info["role"], "dept": info["dept"], "budget": budget,
                     "spent": round(spent["cost"], 4), "unpriced": spent["unpriced"],
                     "state": state_of(spent["cost"], budget)})
    return rows


def over_budget(now: float = None) -> dict:
    """profile -> {'budget', 'spent'} for every agent that is over its budget this month."""
    return {r["profile"]: {"budget": r["budget"], "spent": r["spent"]} for r in status(now) if r["state"] == "over"}


def check(profile: str, now: float = None):
    """Raises HTTP 409 when the agent has used up its monthly budget."""
    budget = _load().get(profile)
    if not budget:
        return
    spent = spent_this_month(profile, now)["cost"]
    if spent >= budget:
        raise HTTPException(409, "%s has used its monthly budget ($%.2f of $%.2f). Raise the budget on the Activity page, "
                                 "or wait for the 1st of next month." % (profile, spent, budget))


class BudgetIn(BaseModel):
    monthly_usd: float = None   # empty or 0 removes the budget


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/budgets")
    def get_budgets():
        return {"month_start": month_start(), "warn": WARN, "agents": status()}

    @router.put("/api/budgets/{profile}")
    def set_budget(profile: str, body: BudgetIn):
        if profile not in activity_api.profiles():
            raise HTTPException(404, "unknown agent: %s" % profile)
        agents = _load()
        if body.monthly_usd and body.monthly_usd > 0:
            if body.monthly_usd > 100000:
                raise HTTPException(400, "that budget is not a sensible number of dollars")
            agents[profile] = round(body.monthly_usd, 2)
        else:
            agents.pop(profile, None)
        _save(agents)
        return {"month_start": month_start(), "warn": WARN, "agents": status()}

    return router
