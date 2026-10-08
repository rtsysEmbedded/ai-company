"""Test: the manager dispatches to the team leads, and the leads work with each other
(scripts/apply_team.py, departments/engine/dept.py).

Runs on a temporary copy of the company folder against a temporary Hermes home. Run: .venv/bin/python tests/test_team_leads.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="team-leads-test-")
SW, HOME = os.path.join(TMP, "company"), os.path.join(TMP, "hermes")
os.makedirs(os.path.join(SW, "scripts"))
shutil.copy(os.path.join(ROOT, "team.yaml"), SW)
shutil.copy(os.path.join(ROOT, "scripts", "apply_team.py"), os.path.join(SW, "scripts"))
ignore = shutil.ignore_patterns(".DS_Store", ".env", "__pycache__", "tests")
shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(SW, "policies"), ignore=ignore)
shutil.copytree(os.path.join(ROOT, "departments"), os.path.join(SW, "departments"), ignore=ignore)
for name in ("sw-lead", "hw-lead", "web-lead"):
    os.makedirs(os.path.join(HOME, "profiles", name))
ENV = {k: v for k, v in os.environ.items() if not k.startswith("DEPT_")}
ENV.update(HERMES_HOME=HOME)
TEAM = os.path.join(SW, "team.yaml")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def team():
    with open(TEAM, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def apply(*roles):
    r = subprocess.run([sys.executable, os.path.join(SW, "scripts", "apply_team.py"), "--soul-only", "--only", *roles],
                       capture_output=True, text=True, env=ENV)
    return (r.stdout + r.stderr)[-400:]


def soul(profile=None):
    path = os.path.join(HOME, "profiles", profile, "SOUL.md") if profile else os.path.join(HOME, "SOUL.md")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def lead_rules(department, lead):
    code = ("import sys; sys.path.insert(0, %r); import dept; print(dept.routing_rules(%r, dept.load_config(%r)))"
            % (os.path.join(SW, "departments", "engine"), lead, department))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV).stdout


def tools_rules(department, role):
    code = ("import sys; sys.path.insert(0, %r); import dept; print(dept.tools_rules(%r, dept.load_config(%r)))"
            % (os.path.join(SW, "departments", "engine"), role, department))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV).stdout


def main():
    doc = team()
    with open(os.path.join(SW, ".env"), "w") as handle:
        handle.write("\n".join("%s=sk-test-not-a-real-key" % s["key_env"] for s in doc["roles"].values()) + "\n")
    check("team.yaml names a software lead that is not the manager",
          doc["routing"].get("lead") in doc["roles"] and doc["routing"]["lead"] != "manager")
    out = apply("manager", "sw-lead")
    manager, lead = soul(), soul("sw-lead")

    check("the manager lists every team by its lead", all("**%s** leads" % n in manager for n in ("sw-lead", "hw-lead", "web-lead")), out)
    check("the manager dispatches one task per team and does not split the work",
          "create ONE task" in manager and "# Your team" not in manager and "**backend**:" not in manager)
    check("the manager ends a lead's task with the line that makes the lead delegate",
          "End the body with this line, word for word" in manager and "do not block it for a missing tool" in manager)
    check("the manager tells the leads to agree with each other", "the leads agree on it with each other" in manager)
    check("the manager's own rules are the company's", "You dispatch for the whole company" in manager)

    check("the software lead has the specialists, without the manager or itself",
          "# Your team" in lead and "**backend**:" in lead and "**manager**" not in lead and "**sw-lead**:" not in lead)
    check("the software lead keeps review, security review and escalation",
          "Do not write a review instruction into a body" in lead and "**reviewer**" in lead and "review task for **security**" in lead and "**architect**" in lead)
    check("the software lead plans a board task and ends it", "# When a task on the board is assigned to you" in lead and "kanban_complete" in lead)
    check("the software lead knows the other leads and how to reach them",
          "# Other teams" in lead and "**hw-lead** leads" in lead and "**web-lead** leads" in lead
          and "[request]" in lead and "docs/interfaces.md" in lead and "a comment wakes nobody" in lead and "**sw-lead** leads" not in lead)
    check("every lead closes the loop and uses the goal loop for its specialists",
          all("check and report" in text and "goal_mode: true" in text and "# When a specialist is blocked" in text
              for text in (lead, lead_rules("hardware", "hw-lead"), lead_rules("web", "web-lead"))))

    hw, web = lead_rules("hardware", "hw-lead"), lead_rules("web", "web-lead")
    check("the hardware lead can work with the software and web leads",
          "# Other teams" in hw and "**sw-lead** leads Software" in hw and "**web-lead** leads" in hw and "**hw-lead** leads" not in hw, hw[-300:])
    check("the web lead can work with the software and hardware leads",
          "**sw-lead** leads Software" in web and "**hw-lead** leads" in web and "**web-lead** leads" not in web)
    check("a lead ends a task for another lead with the same line", "do not block it for a missing tool" in hw and "do not block it for a missing tool" in lead)
    check("a lead's assignee rule allows another team's lead", "the only exception is another team's lead" in hw)
    check("a specialist's rules do not change", "# Other teams" not in lead_rules("hardware", "hw-layout"))

    check("the lead sees which tools each specialist has", "**hw-schematic** (Schematic engineer; tools: kicad)" in hw
          and "**hw-research** (Component researcher; tools: no special tools)" in hw)
    lead_tools, worker_tools = tools_rules("hardware", "hw-lead"), tools_rules("hardware", "hw-research")
    check("the lead knows its own tools and how to find them", "its tools are named `mcp__kicad__...`" in lead_tools
          and "`tool_search`" in lead_tools and "`tool_call`" in lead_tools)
    check("a missing tool makes the lead delegate, not block", "never a reason to block a task" in lead_tools
          and "give that work to the team member who has the tool" in lead_tools)
    check("a specialist without the tool blocks and names it for the lead", "no special tools of this department" in worker_tools
          and "name the tool" in worker_tools and "**hw-lead**" in worker_tools and "never a reason to block" not in worker_tools)

    shutil.rmtree(os.path.join(HOME, "profiles", "web-lead"))
    check("a team whose lead has no profile is not offered", "web-lead" not in lead_rules("hardware", "hw-lead"))

    # without a lead the manager leads the software team itself, as before
    text = open(TEAM, encoding="utf-8").read()
    with open(TEAM, "w", encoding="utf-8") as handle:
        handle.write("\n".join(l for l in text.splitlines() if not l.startswith("  lead:")) + "\n")
    apply("manager")
    manager = soul()
    check("without a lead the manager splits software work itself", "# Your team" in manager and "**backend**:" in manager
          and "# Other departments" in manager and "**hw-lead** leads" in manager)
    check("without a software lead the other leads do not offer one", "Software" not in lead_rules("hardware", "hw-lead"))

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
