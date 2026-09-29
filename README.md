# ai-company
A portable AI dev team: a manager agent routes coding tasks to specialist agents (Hermes profiles),
all sharing one local knowledge base (MLX embeddings + LanceDB on Apple Silicon).

## Quick start (new machine)
1. `cp .env.example .env` and fill in one OpenRouter key per agent.
2. `cp machine.example.yaml machine.yaml` and adjust if needed.
3. `./scripts/bootstrap.sh`

See docs/ARCHITECTURE.md. Team: team.yaml. Rules: policies/. Library: knowledge/.
