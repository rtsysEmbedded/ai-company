# How the department engine works

## The pieces

```
departments/<name>/department.json + roles/*.md + policies/*.md      departments/.env
            |
            |  ./dept apply   (or the web interface's "Save and apply")
            v
  Hermes profiles  ~/.hermes/profiles/hw-lead, hw-schematic, rs-lead, ...
            |
            |  tasks on Hermes's kanban board, started by Hermes's dispatcher
            v
  each agent: its model via OpenRouter + the tools its role was given + their skills
```

A department is data: a folder with `department.json` (roles, models, routing), one rules file
per role and the department's shared policies. The engine in `departments/engine/` is the same
for all of them.

The department folders hold the configuration; the engine holds the scripts. Hermes does the running, and never reads
these folders: `apply` copies what is needed into the profiles. The only thing here
that keeps running is the web interface, as a small background service for your convenience;
the agents work the same whether it is up or not.

## What `apply` writes for each role

| What | Where | How |
|---|---|---|
| The profile itself | `~/.hermes/profiles/<role>/` | `hermes profile create` |
| Model | `config.yaml`: `model.default`, `model.provider` | `hermes -p <role> config set` |
| Fallback model | `config.yaml`: `fallback_providers` | `hermes -p <role> config set` |
| Thinking effort | `config.yaml`: `agent.reasoning_effort` | `hermes -p <role> config set` |
| Task-board tools (lead) | `config.yaml`: `platform_toolsets.cli` | `hermes -p <role> tools enable kanban` |
| Tools that are MCP servers (KiCad, FreeCAD, knowledge base) | `config.yaml`: `mcp_servers` | `hermes -p <role> mcp add` / `remove` |
| A tool's skill, if it has one | `skills/<tool>/SKILL.md` | file copy |
| Role rules | `SOUL.md`, between the `department:begin` / `end` markers | file write |
| API key | `.env`: `OPENROUTER_API_KEY` (mode 600) | file write |
| Description | `profile.yaml` | `hermes profile describe` |

Hermes's own configuration file is only changed through the `hermes` command, never edited
directly, so the scripts do not depend on its internal format. `apply` is idempotent: it compares
the desired state with what the profile has and only acts on differences. `./dept status` shows
the same comparison without changing anything.

Role names are Hermes profile names, so they must be unique across the whole company. Each
department uses a prefix (`hw-`, `rs-`, ...), and the engine refuses a name already used by
another department or by the software department. Hermes also creates a launcher for each
profile (`hw-lead` is the same as `hermes -p hw-lead`).

## The rules an agent gets

The generated block in a profile's `SOUL.md` is built from, in order: the role's title and
description, every file in the department's `policies/`, the role's own `roles/<role>.md`, the
routing rules (for the lead: the team roster and how to create tasks; for the others: how to
finish, request review or block), and a paragraph for each tool that comes with rules of its own
(the knowledge base). `{projects_dir}` in the text is replaced by the department's projects folder.

## Tasks and the dispatcher

Every department uses Hermes's standard kanban board. The lead has the task-board tools and creates
tasks with an assignee. Hermes's dispatcher starts the assigned profile for each ready task; the
worker gets its task-lifecycle tools automatically.

The dispatcher runs inside the Hermes gateway (`hermes gateway start`). If a machine already runs
a standalone dispatcher (`hermes kanban daemon`), that one serves every department's tasks; do not
run both. `./dept status` reports whether either is running.

Limits on how many agents run at once are Hermes settings (`kanban.max_in_progress`,
`kanban.max_in_progress_per_profile` in the main profile's configuration). In this company they
are written by the software department's `team.yaml` routing and apply to all departments.

## Next to the software department

The software department (`team.yaml`, `scripts/apply_team.py`, its panel and knowledge base) is
not run by this engine and is not changed by it:

- The engine never touches the software team's profiles, and refuses role names that would clash.
- The software manager does not know other departments' roles unless you tell it.
- `scripts/escalate.py` only escalates the software team's own tasks, so a failing hardware task
  is not handed to the software architect.
- Concurrency limits are shared, because they belong to the board.

## Tools

`engine/tools.json` lists what a role can be given:

| Tool | Kind | What `apply` does |
|---|---|---|
| `kanban` | Hermes toolset | enables the task-board tools (needed by a lead) |
| `kicad`, `freecad` | MCP server + skill | registers the server installed in `~/.local/share/<name>-mcp` and copies the skill |
| `kb` | MCP server | registers the software department's knowledge base, with the role's name as `KB_ROLE`, and adds the knowledge-base paragraph to the role's rules |
| `modern-web-guidance` | Skill only | copies the skill from the main Hermes profile (`~/.hermes/skills/<name>/SKILL.md`) into the role's profile; unavailable when it is not installed there |

The FreeCAD and KiCad servers come from `packages/freecad-mcp` and `packages/kicad-mcp`, complete
copies of the two tool packages, each with its own installer, tests and README. To update them
from newer source folders, run `departments/engine/sync-packages.sh`. A tool that is not installed
on a machine is shown as unavailable in the web interface and cannot be newly ticked.

## The web interface

`engine/ui/server.py` is a small web server from Python's standard library; `engine/ui/index.html`
is one page with no external dependencies. It shows every department and links to the software
department's own panel (logging the browser in with that panel's token, as `scripts/panel-open` does). The server calls the same functions as the command line.

It runs as a background service installed by `engine/service.sh`: a launchd agent
(`com.aicompany.departments`) on macOS, a systemd user service (`ai-company-departments.service`) on Linux.
Both start it at login and restart it if it stops. Its log is `data/departments/logs/ui.log`; the login
token is never written there.

Security:

- It listens on `127.0.0.1` only, so it is not reachable from other machines.
- It needs a random token, stored in `data/departments/ui_token` (mode 600). `./dept open` opens a login link
  that sets a cookie. Without the cookie every request is refused.
- Requests that change anything must carry a custom header, which a web page from another site
  cannot send, and the `Host` header must be this server (this blocks DNS-rebinding tricks).
- The API key can be written and removed but never read back. It is not in any response.
- Only known fields of existing roles are saved; anything else in a request is ignored. A
  department name in a request must be an existing department folder.

## Keys

`departments/.env` holds `OPENROUTER_API_KEY` (shared by all of the engine's departments) and,
optionally, `OPENROUTER_API_KEY_<DEPARTMENT>` (for example `OPENROUTER_API_KEY_HARDWARE`) and
`OPENROUTER_API_KEY_<ROLE>` (for example `OPENROUTER_API_KEY_HW_LEAD`). The most specific one
wins, so a department or a single role can have its own spending limit. `apply` writes the right
key into each profile's `.env`, and the web interface does the same immediately whenever a key is
saved or removed, for the profiles that already exist. Removing the last key that applies to a
role also removes it from that role's profile. The software department's keys are separate, in the company
folder's `.env`.
Both files are readable only by you. `departments/.env` is listed in `.gitignore`.

## Tests

`engine/tests/test_departments.py` sets `HERMES_HOME` to a temporary folder, copies the department
folders to another, and runs the real `hermes` command against them, so real profiles are created, checked through Hermes itself, changed and
deleted without touching your own. It covers two departments at once, including creating one from
the template. OpenRouter is replaced by a local stub. The test also starts
the web server and checks its access control and every endpoint.
