# ai-company
A portable AI dev team: a manager agent routes coding tasks to specialist agents (Hermes profiles),
all sharing one local knowledge base (MLX embeddings + LanceDB on Apple Silicon).

## Quick start (new machine)
1. `cp .env.example .env` and fill in one OpenRouter key per agent.
2. `cp machine.example.yaml machine.yaml` and adjust if needed.
3. `./scripts/bootstrap.sh`

See docs/ARCHITECTURE.md. Team: team.yaml. Rules: policies/. Library: knowledge/.

## Departments
The team above is the **software department**. Other departments (hardware, and any you add:
research, sales, ...) live in `departments/` and run on one shared department engine.

- `./dept open` opens one page listing every department, with a link to the software panel.
- `./dept list`, `./dept status`, `./dept apply` manage them from the terminal.
- `./dept new research --title "Research" --prefix rs` adds a department.
- `departments/install.sh` sets them up on another machine.

See departments/README.md.
