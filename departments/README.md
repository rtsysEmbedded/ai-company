# Departments

The company is organised in departments. Each department is a team of Hermes agents: one lead
you talk to, and specialists the lead hands tasks to over Hermes's kanban board.

| Department | Where it is defined | How it is managed |
|---|---|---|
| **Software** | `team.yaml`, `policies/`, `scripts/apply_team.py` (company folder root) | Its own control panel, `scripts/panel-open` |
| **Hardware** | `departments/hardware/` | The department engine, `./dept` |
| **Web Development** | `departments/web/` | The department engine, `./dept` |
| Research, Sales, ... | `departments/<name>/` when you add them | The department engine, `./dept` |

The software department was built first and keeps its own tooling (routing, knowledge base,
panel). Everything under `departments/` runs on one shared **department engine**: a department
there is only a folder of roles and rules, so adding one needs no new code.

## The control panel covers every department

`scripts/panel-open` opens the control panel on `http://127.0.0.1:8770`. Its sidebar lists the
software department and every department of the engine, each with the same tabs: Team, Routing,
Rules, Keys, Models and Apply. There you can also add a department, add or delete roles, and edit
`roles/*.md` and `policies/*.md`. Knowledge and Services are shared by the whole company. The
routes are in `services/panel/dept_api.py` and call the same engine functions as `./dept`;
`tests/test_panel_departments.py` checks them against a temporary copy.

The older single page below (`./dept open`, port 8780) still works and does a subset of this.

## One page for everything

```bash
./dept open
```

opens `http://127.0.0.1:8780`. It lists every department, including a link that logs you in to
the software panel. For the engine's departments you can there:

- pick a model and fallback per role from OpenRouter's live list, with prices and benchmark scores,
- enable or disable roles, choose their tools and thinking effort, edit title and description,
- set API keys: one shared key, a key per department, or a key for a single role right in that
  role's card (each with its own spending limit). Keys take effect at once, without applying,
- set the projects folder, lead, reviewer, review policy and time limit per task,
- see what differs from what Hermes actually has, and apply it,
- add a new department.

The page runs as a background service (started at login, restarted if it stops), like the
software panel on port 8770, so both are ready whenever you use Hermes.

## Adding a department

In the web interface press **Add a department**, or:

```bash
./dept new research --title "Research" --prefix rs
```

This creates `departments/research/` with three starter roles: `rs-lead`, `rs-specialist`,
`rs-review`. Then make it yours:

1. **Roles.** In `department.json` (or the web interface) rename, add or remove roles. A role
   name is also its Hermes profile name, so keep the prefix to avoid clashes.
2. **Descriptions.** Each role's description tells the lead what that role is good at.
3. **Rules.** `roles/<role>.md` says how that role works; `policies/*.md` holds the rules shared
   by the whole department. The template's rules already forbid sending messages, publishing or
   spending money without your confirmation; keep that for a sales department.
4. **Tools.** Tick what each role needs. The lead needs the task board. Research and sales roles
   usually need none of the CAD tools; Hermes's built-in web search and browser are always there.
   The company knowledge base can be given to any role.
5. **Apply.** `./dept apply research`, or "Save and apply to Hermes" in the web interface.
6. **Talk to its lead:** `hermes -p rs-lead`.

To offer a new kind of tool to roles (another MCP server), add an entry to
`departments/engine/tools.json`.

## Commands

```bash
./dept open                 # the web interface
./dept list                 # departments and their state
./dept status [department]  # what is configured, what Hermes has, what is missing
./dept apply [department]   # make the Hermes profiles match (all departments if none is named)
./dept validate             # check every department's configuration
./dept new NAME --title T --prefix P
./dept models --refresh     # update OpenRouter's model list and prices
./dept remove DEPARTMENT    # delete that department's Hermes profiles
./dept service status       # the web interface's background service (also: install, uninstall)
python3 departments/engine/tests/test_departments.py   # full test in a temporary Hermes home
```

## Setting up on another machine

Copy the company folder (without `data/` and `.venv/`, which are machine-specific), then:

```bash
departments/install.sh --install-hermes --install-apps
```

It checks Python, installs Hermes Agent if missing, installs the FreeCAD and KiCad tools from
`packages/` (and the applications with `--install-apps`), tests everything, creates the Hermes
profiles of every department, and installs the web interface service. Then add your OpenRouter
key in the web interface and apply. Options: `departments/install.sh --help`.

This sets up the engine's departments only. They need nothing but Python 3.8+ and Hermes, on
macOS or Linux. The software department's knowledge base has its own requirements (Apple
Silicon, Python 3.12, `uv`) and its own setup (`scripts/install_services.sh`).

## How the departments share one machine

- **One task board.** All departments use Hermes's default kanban board and one dispatcher. The
  limits on how many agents run at once (`team.yaml` routing: `max_parallel`, `max_per_profile`)
  therefore apply to everyone together.
- **Separate leads.** The software manager does not know the other departments' roles. Talk to a
  department's lead directly, or tell the manager to give that kind of work to the lead by name.
- **Escalation stays inside the software team.** `scripts/escalate.py` hands repeatedly failing
  tasks to the software architect; it only does so for the software team's own tasks.
- **Separate keys.** The software department's keys are in the company folder's `.env`. The
  engine's departments use `departments/.env`: a role's own key wins over its department's key,
  which wins over the shared one.
- **Shared knowledge base.** Any role can be given the `kb` tool, which is the software
  department's knowledge base (`knowledge/`), for example for datasheets.

## Good to know

- **Cost.** Agents spend your OpenRouter credit. Set a spending limit on each key in OpenRouter.
- **Review the output.** A reviewer role on a different model checks the work, and the hardware
  agents run KiCad's rule checks. That catches rule violations, not wrong results. Have a person
  check anything before it is manufactured, sent or published.
- **The agents can run commands on your machine** through Hermes's terminal and file tools.
- **Model choices age quickly.** See `hardware/docs/MODELS.md` for how the hardware models were
  chosen (October 2026) and how to re-check.

### What was tested, and what was not

Tested on macOS with Hermes Agent 0.21.2:

- `engine/tests/test_departments.py` creates real Hermes profiles in a temporary Hermes home for
  two departments and checks models, tools, skills, rules, keys, the web interface's API and its
  access control.
- The web page was driven in headless Chrome: department cards, switching, creating a
  department, editing, validation errors, keys per department, apply.
- The knowledge-base tool connects on a new department's role (6 tools discovered).
- The background service starts, answers, and comes back after being killed.

Not tested:

- **A real project run in any of the engine's departments.** No task has been planned by a lead
  and carried out by specialists, because that needs your API key and spends your credit.
- Installing Hermes, KiCad or FreeCAD from scratch, Linux (including the systemd service), and
  starting a dispatcher on a machine that has none (`hermes gateway start`).

## What is in this folder

```
README.md               this file
install.sh, uninstall.sh  set up / remove the engine's departments on a machine
.env                    OpenRouter keys for these departments (created by the web interface; private)
engine/dept.py          the department engine: validate, status, apply, new, remove
engine/tools.json       the tools a role can be given
engine/ui/              the web interface (server.py, index.html)
engine/service.sh       runs the web interface as a background service
engine/benchmarks.json  general benchmark scores shown next to models
engine/tests/           end-to-end test
_template/              what a new department starts from
hardware/               the hardware department: department.json, roles/, policies/, presets/, docs/
docs/ARCHITECTURE.md    how the engine maps a department onto Hermes
```

In the company folder: `dept` is the command, `packages/` holds the FreeCAD and KiCad tool
packages, and `data/departments/` holds the model-list cache, the web interface's token and logs.
