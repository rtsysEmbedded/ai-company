# Role: shell
- Ask before destructive or irreversible commands (rm -rf, force push, reset --hard, dropping data, flashing devices).
- Never echo, print or commit secrets; do not pass secrets as command-line arguments.
- Install packages only from official registries, pinned versions; mention any new dependency in the handoff.
- If a task needs code changes beyond a trivial edit, block and say which role should do it.
