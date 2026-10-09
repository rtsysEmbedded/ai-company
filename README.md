# ai-company

**Run a team of AI agents like a small company, not like one chatbot.**

You give a request to one *manager*. The manager does not do the work: it hands the job to the
*lead* of the right department (software, hardware, web, or any you add). The lead plans, splits the
job into tasks and hands each to a *specialist*. Specialists work until the task is really done, a
reviewer checks it, and the lead closes the job with a report. Everything runs on
[Hermes](https://github.com/NousResearch/hermes-agent) profiles and its kanban board, on your own
machine, with cheap models.

```
you ──> manager ──> department lead ──> specialists ──> reviewer ──> lead's check & report ──> you
                         │  ▲                │
                         │  └── blocked? ────┘      leads talk to each other with [request] tasks
                         └── shared company knowledge base (read before a job, written when it closes)
```

## The idea

A single agent that plans, codes, reviews and judges its own work is slow, expensive and easy to
fool. A company solves this with roles and process. `ai-company` turns that into configuration:

- **Roles, not prompts.** A team is a folder of roles (`team.yaml`, `departments/<name>/`). Each
  role has its own model, tools, rules and API key.
- **Process is enforced, not requested.** Rules in a prompt get ignored by small models, so the
  important ones are enforced by hooks that block the wrong action.
- **Knowledge is a company asset.** What one agent learns, every agent can find later.

## Advantages

| | What you get |
|---|---|
| **Leads must delegate** | A guard hook stops a lead from running commands or writing files itself, so the plan is always made and handed out, even by a model that would rather do it alone. |
| **Work is checked until done** | Specialists run in a goal loop. The last task of every job is the lead's own `check and report`, which depends on all specialist tasks. |
| **Blocked work finds its owner** | A blocked task goes to the lead, not to you. The lead answers, and the work continues as a new task that carries the answer. |
| **Departments cooperate** | Leads hand work to each other with `[request]` tasks and write agreements to `docs/interfaces.md`. |
| **Cheap models are enough** | The structure carries the quality: small models with guards, with a stronger model only where it pays (manager, leads, security review). |
| **You see the money** | One API key per role, each with its own spending limit. The panel shows spend per role. |
| **Shared memory** | A local knowledge base (MLX embeddings, LanceDB) with typed notes (lesson, error, howto, research, decision). Agents search it before a job and write to it when they close one. |
| **Nothing is a black box** | The control panel shows who is working right now, the task chain per project, live agent output, and an inbox of finished jobs, open questions and stopped tasks. |
| **Add a department with one command** | `./dept new research --title "Research" --prefix rs` adds a team that runs on the shared engine, with no new code. |
| **Yours** | Local files, git history, plain Markdown and YAML. Back up or move everything by copying a folder. |

## Quick start (Apple Silicon Mac)

1. `cp .env.example .env` and fill in one OpenRouter key per agent.
2. `cp machine.example.yaml machine.yaml` and adjust if needed.
3. `./scripts/bootstrap.sh`

The control panel then runs on `http://127.0.0.1:8770`. Team definition: `team.yaml`. Rules:
`policies/`. Library: `knowledge/`. More: `docs/ARCHITECTURE.md`.

## Departments

The team above is the **software department**. Other departments (hardware, web, and any you add:
research, sales, ...) live in `departments/` and run on one shared department engine.

- `./dept open` opens one page listing every department, with a link to the software panel.
- `./dept list`, `./dept status`, `./dept apply` manage them from the terminal.
- `./dept new research --title "Research" --prefix rs` adds a department.
- `departments/install.sh` sets them up on another machine.

See [departments/README.md](departments/README.md).

## Repository layout

| Path | What is in it |
|---|---|
| `team.yaml`, `policies/`, `scripts/` | Software department definition and the script that applies it to Hermes |
| `departments/` | Department engine, hardware and web departments, lead guard hook |
| `services/panel/` | Control panel (Flow, Office, Activity, Inbox, Knowledge, Backup, ...) |
| `services/kb/`, `services/embedder/` | Company knowledge base and local embeddings |
| `packages/` | MCP servers for FreeCAD and KiCad used by the hardware team |
| `knowledge/` | Notes the agents wrote while working (lessons, errors, research) |
| `tests/` | Tests for the panel, guards and routing |

## Status

Personal project, in daily use and changing quickly. It targets macOS on Apple Silicon and assumes
the Hermes agent runtime and OpenRouter. Expect rough edges.

## License

MIT, see [LICENSE](LICENSE).
