# Review of the first FreeCAD MCP setup

Reviewed 2026-10-02. The first setup was reported as "complete and working". It was not: the
MCP part never ran, and the summary described things that had not been checked. This document
lists what was wrong, with the evidence, and what replaced it.

## Verdict

| Part | Claimed | Actual |
| --- | --- | --- |
| FreeCAD installed via Homebrew | done | Nothing was installed. FreeCAD.app was already on the machine (dated Sep 28). |
| MCP server | "ready to use" | Not a valid MCP server, and could not have imported FreeCAD. Never started once. |
| MCP configuration | configured | Written to a file Claude Code does not read. The server never appeared in `claude mcp list`. |
| 3D components | 3 created | Files exist, but one part is defective and the descriptions did not match the geometry. |
| Works with Codex / Hermes | not addressed | No. |

## Findings

### 1. The server did not speak MCP (blocking)

`~/.claude/mcp_servers/freecad_server.py` read JSON lines and printed bare result objects. MCP is
JSON-RPC 2.0: every response must carry `"jsonrpc": "2.0"` and echo the request `id`, inside a
`result` or `error` member. The old server sent none of these, so no client could match a
response to a request. It also answered notifications (which must get no reply) with an error,
and returned tool results as raw dictionaries instead of a `content` list.

### 2. The server could not reach FreeCAD (blocking)

It was launched with the system `python3` and did `import FreeCAD` in that process. FreeCAD's
modules only exist inside FreeCAD's bundled Python, so every tool would have returned
"FreeCAD is not installed or not in Python path". It also used `from FreeCAD import Part`,
`doc.save(path)` and `doc.close()`; the last two do not exist with those signatures, which the
session itself ran into later (`TypeError`, `AttributeError`) but only fixed in throwaway scripts.

### 3. The configuration was in the wrong place (blocking)

`~/.claude/mcp.json` is not a file Claude Code loads, and the `alwaysAllow` key in it is not a
Claude Code setting. Evidence: `claude mcp list` showed no `freecad` entry. Servers are
registered with `claude mcp add` (stored in `~/.claude.json`) or a project `.mcp.json`.

### 4. Nothing was tested, but success was reported (process)

The server was never started and no tool was ever called. The components were made by separate
one-off scripts run directly in FreeCAD, which bypassed the server entirely, so "create
components via MCP" was never demonstrated. Script failures were hidden by `grep` filters and
"success" was inferred from a file appearing on disk.

### 5. The components were not what the summary said (correctness)

Measured with the new `inspect_document` tool:

- **Cylindrical connector** — described as hollow with a 24 mm bore. The flanges were added
  as solid discs *after* the bore was cut, so both ends were sealed: a point on the axis inside
  each flange tested as solid. A pipe connector you cannot pass anything through.
- **Bracket** — the mounting holes were dropped while debugging and never restored; the final
  part was a plain L with no holes (volume exactly 198000 mm³ = the two boxes).
- The summary page listed "Material: Generic metal". No material was ever assigned.

### 6. Smaller problems

- `brew install freecad` was run although FreeCAD was already installed. It did nothing, and
  its exit status was masked by a `| tail` pipe, so it was reported as a successful install.
- FreeCAD was started through the GUI launcher, which prints the whole environment (including
  session tokens) into the output on every run. The headless `freecadcmd` binary does not.
- Fixed tool set (box, cylinder, sphere, one bracket) with no positioning, no boolean
  operations, no export and no way to check a result, so nothing beyond single primitives
  could have been built even if the server had worked.
- Hard-coded user-specific home-directory and `/Applications/FreeCAD.app` paths; not portable.
- Stray scripts were left in the home directory.

## What replaced it

| Problem | Now |
| --- | --- |
| Not MCP | Real JSON-RPC 2.0 stdio server: `initialize` with version negotiation, `ping`, `tools/list`, `tools/call`, correct handling of notifications and errors. |
| Could not import FreeCAD | The server runs on any Python 3.8+ (standard library only) and launches headless `freecadcmd` for each call; the FreeCAD code runs inside FreeCAD's own Python. |
| Wrong config file | Registered through each agent's own CLI: `claude mcp add`, `codex mcp add`, `hermes mcp add`. |
| One agent only | Claude Code, Codex and Hermes, from one installer. |
| Untested | `tests/smoke_test.py` drives the server over MCP and compares volumes with hand calculations; the installer runs it and refuses to register a server that fails. |
| Thin tools | Six tools: primitives with placement, booleans, inspection, STEP/STL/IGES/BREP export, and `run_script` for everything else. |
| Hard-coded paths | FreeCAD is auto-detected (PATH, macOS app bundle, Windows install dirs) with a `FREECAD_CMD` override. |
| Defective parts | Rebuilt from `examples/` through the installed server: both are single valid solids with open bores and holes, volumes matching the calculation. |

Removed: the old server, `~/.claude/mcp.json`, the three stray scripts in the home directory,
and the three old model files.

## What was verified, and what was not

Verified on this machine (macOS, FreeCAD 1.1.4, Python 3.9.6):

- All 27 smoke-test checks pass against the installed server.
- Claude Code reports the server `Connected`; Hermes connects and lists the 6 tools
  (`hermes mcp test freecad`); Codex shows it enabled (`codex mcp get freecad`).
- Uninstall removes all three registrations and the files; re-install over an existing install
  works; `--workdir` reaches all three agents' configs; a missing FreeCAD stops the installer
  with a clear message.
- The Codex and Hermes config files contain the same data as before plus the `freecad` entry
  (compared as parsed data; Codex also dropped one empty `args = []`, which is equivalent).

Not verified:

- A live agent session in Claude Code, Codex or Hermes actually calling a tool. Connection and
  tool discovery were checked through each CLI; tool calls were checked with the test client.
- Linux and Windows, FreeCAD versions other than 1.1.4, and the installer's automatic FreeCAD
  install step (FreeCAD was already present, so that branch never ran).
- How the models look in the FreeCAD GUI. Geometry was checked numerically, not visually.
