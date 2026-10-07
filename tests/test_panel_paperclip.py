"""Paperclip section of the Activity page: runs are paired with default-profile Hermes sessions."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "panel"))

import activity_api  # noqa: E402
import paperclip_usage  # noqa: E402


def _session(sid, source, started, tokens, cost=0.0, status="unknown"):
    return {"id": sid, "source": source, "model": "auto", "started_at": started, "input_tokens": tokens,
            "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
            "estimated_cost_usd": cost, "actual_cost_usd": None, "cost_status": status, "api_call_count": 1}


def _run(rid, agent, task, started, status="succeeded", adapter="hermes_local"):
    return {"id": rid, "company": "Co", "status": status, "agent": agent, "agent_model": "m/x", "adapter": adapter,
            "task": task, "title": "T " + task, "task_status": "done", "started_at": started, "ended_at": started + 30}


def _report(runs, sessions, since=0.0, available=True):
    paperclip_usage._cache.update(at=1e18, data={"available": available, "url": "http://x", "runs": runs})
    try:
        return paperclip_usage.report(sessions, since, activity_api._usage, activity_api._add, now=10_000)
    finally:
        paperclip_usage._cache.update(at=0.0, data=None)


def test_runs_are_paired_by_start_time_and_grouped():
    runs = [_run("r1", "Fw", "EMB-1", 1000), _run("r2", "Fw", "EMB-1", 1100), _run("r3", "Hw", "EMB-2", 1200)]
    sessions = [_session("s1", "tool", 1002, 10), _session("s2", "tool", 1103, 20), _session("s3", "tool", 1202, 40),
                _session("chat", "cli", 1001, 999)]  # the manager's own chat must not be taken
    section, used = _report(runs, sessions)
    assert used == {"s1", "s2", "s3"}
    assert section["total"]["total"] == 70 and section["unmatched"] == 0
    tasks = {t["task"]: t for t in section["tasks"]}
    assert tasks["EMB-1"]["usage"]["total"] == 30 and len(tasks["EMB-1"]["runs"]) == 2
    assert [a["agent"] for a in section["by_agent"]] == ["Hw", "Fw"]


def test_period_filter_does_not_shift_matches():
    runs = [_run("old", "Fw", "EMB-1", 1000), _run("new", "Fw", "EMB-2", 1030)]
    sessions = [_session("s-old", "tool", 1002, 10), _session("s-new", "tool", 1032, 20)]
    section, used = _report(runs, sessions, since=1020)
    assert [t["task"] for t in section["tasks"]] == ["EMB-2"] and section["total"]["total"] == 20
    assert used == {"s-old", "s-new"}


def test_run_without_session_and_other_adapters():
    runs = [_run("r1", "Fw", "EMB-1", 1000), _run("r2", "Cl", "EMB-9", 1000, adapter="claude_local")]
    section, used = _report(runs, [])
    assert used == set() and section["unmatched"] == 1
    assert [t["task"] for t in section["tasks"]] == ["EMB-1"] and section["tasks"][0]["runs"][0]["usage"] is None


def test_paperclip_down_hides_nothing_else():
    section, used = _report([], [_session("s1", "tool", 1002, 10)], available=False)
    assert section["available"] is False and section["tasks"] == [] and used == set()


def test_unpriced_router_session_is_unknown_not_free():
    assert activity_api._usage(_session("s", "tool", 1, 10))["cost_unknown"] == 1
    assert activity_api._usage(_session("s", "tool", 1, 10, cost=0.02, status="estimated"))["cost_unknown"] == 0
    assert activity_api._usage(_session("s", "tool", 1, 10, cost=-5.0))["cost_unknown"] == 1
