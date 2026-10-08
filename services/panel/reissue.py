"""Continue a blocked task with an answer that is part of the task itself.

A task in Hermes' goal loop is judged against its title and body only; an answer that arrives as a comment is
invisible to the judge, which then refuses the completion ("not documented") and the task blocks again. So an
answered goal-loop task is put on the board again with the answer written into its body; the tasks that waited
for the old one wait for the new one, and the old one is archived.
"""
import re

REISSUED = "[ai-company continued as]"


def is_goal_task(detail: dict) -> bool:
    """True when the task was created with the goal loop (`show --json` detail)."""
    return any(e.get("kind") == "created" and (e.get("payload") or {}).get("goal_mode") for e in detail.get("events", []))


def with_answer(kanban, detail: dict, who: str, answer: str, question: str = "", goal_turns: int = 6) -> str:
    """Create the continuation of a blocked task and retire the old one. Returns the new task id, or '' if it failed.

    `kanban(*args, check=...)` runs `hermes kanban` and returns its output."""
    task = detail.get("task") or {}
    body = "%s\n\nANSWER from %s%s:\n%s\nThis answer is part of the task. Use it; you do not have to ask again or verify it." % (
        (task.get("body") or "").rstrip(), who, ' to your question "%s"' % question.strip()[:600] if question.strip() else "", answer.strip())
    cmd = ["create", (task.get("title") or task["id"])[:160], "--assignee", task.get("assignee") or "", "--body", body[:20000],
           "--created-by", task.get("created_by") or "ai-company"]
    if is_goal_task(detail):
        cmd += ["--goal", "--goal-max-turns", str(goal_turns)]
    if task.get("workspace_path"):
        cmd += ["--workspace", "dir:%s" % task["workspace_path"]]
    if task.get("max_runtime_seconds"):
        cmd += ["--max-runtime", "%dm" % max(5, int(task["max_runtime_seconds"]) // 60)]
    for parent in detail.get("parents") or []:
        cmd += ["--parent", parent]
    found = re.search(r"\b(t_[0-9a-f]+)\b", kanban(*cmd) or "")
    if not found:
        return ""
    new = found.group(1)
    for child in detail.get("children") or []:
        kanban("link", new, child, check=False)
        kanban("unlink", task["id"], child, check=False)
    kanban("comment", task["id"], "%s %s - the answer from %s is in the new task's text." % (REISSUED, new, who))
    kanban("archive", task["id"], check=False)
    return new
