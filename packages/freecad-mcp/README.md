# FreeCAD MCP

Lets an AI coding agent build 3D CAD models in FreeCAD. It is an MCP server, so the same
install works with **Claude Code**, **OpenAI Codex** and **Hermes Agent**.

You ask the agent for a part ("an L-bracket 80 mm wide with two 8 mm holes"), the agent calls
these tools, and you get a `.FCStd` file you can open in FreeCAD plus STEP/STL exports.

## Install on a new machine

Copy this folder to the machine and run:

```bash
./install.sh
```

That is the whole install. The script:

1. finds Python 3.8+ (the server needs nothing else from Python, no `pip install`),
2. copies the server to `~/.local/share/freecad-mcp/`,
3. finds FreeCAD, and offers to install it with Homebrew/apt/dnf/pacman/zypper if it is missing,
4. runs an end-to-end test that builds real parts and checks their volumes,
5. registers the server as `freecad` with every agent CLI it finds (`claude`, `codex`, `hermes`),
6. installs the `freecad` skill for each of those agents (see "Tools and skill" below).

Start a new agent session afterwards so it loads the tools. You can delete or move this folder
after installing; re-run `./install.sh` any time to update or repair.

Options:

| Option | Meaning |
| --- | --- |
| `--clients claude,codex,hermes` | Register only with these (default: all that are installed; `none` to skip) |
| `--install-freecad` | Install FreeCAD without asking if it is missing |
| `--freecad-cmd PATH` | Path to `freecadcmd` if it is somewhere unusual |
| `--workdir DIR` | Where models are stored (default `~/FreeCAD_Components`) |
| `--prefix DIR` | Where the server is installed |
| `--skip-test` | Skip the end-to-end test |
| `--no-skill` | Register the tools only, without the skill |

Requirements: macOS or Linux, Python 3.8+, FreeCAD (0.21 or newer recommended).

## Tools and skill

There are two parts. The **tools** are what the agent can do in FreeCAD. The **skill**
(`skill/freecad/SKILL.md`) is a short guide the agent loads when you ask for a CAD part: the
workflow to follow, FreeCAD code recipes that are known to work, mistakes to avoid, and a
checklist for verifying the geometry before it reports back. It is copied to
`~/.claude/skills/freecad/`, `~/.codex/skills/freecad/` and `~/.hermes/skills/freecad/`.
The tools work without the skill; the skill makes the results more reliable.

| Tool | What it does |
| --- | --- |
| `freecad_info` | FreeCAD version and where models are stored. A quick "is it working" check. |
| `create_primitive` | Add a box, cylinder, sphere, cone or torus, with position and rotation. |
| `boolean` | Fuse, cut or intersect objects. |
| `inspect_document` | List objects with validity, volume, area and bounding box, to verify the result. |
| `export` | Write STEP, STL, IGES or BREP. |
| `run_script` | Run any FreeCAD Python (sketches, fillets, patterns, PartDesign, ...). |

Units are millimetres. Models live in the workdir as `.FCStd` files; a document name like
`bracket` means `~/FreeCAD_Components/bracket.FCStd`.

## Try it

Ask the agent, for example:

- "Use freecad to make a 40 x 20 x 10 mm block with a 6 mm hole through the centre, then export it as STEP."
- "Run examples/bracket.py with the freecad run_script tool into a document called bracket."
- "Inspect bracket and tell me its volume and bounding box."

`examples/` has two complete parts written for `run_script`: an L-bracket with four bolt holes
and a flanged pipe connector.

## Registering by hand

The installer does this for you. If you need to do it manually, the server is started with
`python3 ~/.local/share/freecad-mcp/freecad_mcp.py` (use the full path to both).

Claude Code:

```bash
claude mcp add --scope user freecad -- /usr/bin/python3 ~/.local/share/freecad-mcp/freecad_mcp.py
```

Codex, either `codex mcp add freecad -- /usr/bin/python3 ~/.local/share/freecad-mcp/freecad_mcp.py`
or in `~/.codex/config.toml`:

```toml
[mcp_servers.freecad]
command = "/usr/bin/python3"
args = ["/home/you/.local/share/freecad-mcp/freecad_mcp.py"]
```

Hermes, either `hermes mcp add freecad --command /usr/bin/python3 --args ~/.local/share/freecad-mcp/freecad_mcp.py`
or in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  freecad:
    command: /usr/bin/python3
    args:
      - /home/you/.local/share/freecad-mcp/freecad_mcp.py
    enabled: true
```

Any other MCP client works the same way: it is a stdio server with that command.

## Settings

Environment variables read by the server (the installer sets them for you from its options):

| Variable | Default | Meaning |
| --- | --- | --- |
| `FREECAD_CMD` | auto-detected | Command that runs headless FreeCAD, e.g. `/opt/freecad/bin/freecadcmd` |
| `FREECAD_MCP_WORKDIR` | `~/FreeCAD_Components` | Folder for models and exports |
| `FREECAD_MCP_TIMEOUT` | `120` | Seconds one tool call may take |

## Checking and troubleshooting

```bash
python3 ~/.local/share/freecad-mcp/freecad_mcp.py --check   # can the server run FreeCAD?
python3 tests/smoke_test.py                                 # full end-to-end test
claude mcp list        # or: codex mcp get freecad / hermes mcp test freecad
```

- **"FreeCAD was not found"**: install FreeCAD, or re-run `./install.sh --freecad-cmd /full/path/to/freecadcmd`.
- **The agent does not show the tools**: start a new session; agents load MCP servers at startup.
- **A tool call times out**: heavy models may need a larger `FREECAD_MCP_TIMEOUT`.

## Uninstall

```bash
./uninstall.sh
```

Removes the server, its three registrations and the skill. FreeCAD and your models are left alone.

## Good to know

- **`run_script` runs arbitrary Python** on your machine with your permissions. That is its
  purpose; only connect this server to agents you would trust with a terminal.
- **No GUI.** FreeCAD runs headless, so there are no screenshots and `FreeCADGui` is not
  available. Open the `.FCStd` in FreeCAD to look at the part.
- **Each tool call starts a fresh FreeCAD** (under a second) and saves to disk. Nothing is kept
  in memory between calls.
- The Codex and Hermes CLIs rewrite their whole config file when a server is added (key order
  and formatting change, content does not). The installer keeps the original once as
  `config.toml.bak-freecad-mcp` / `config.yaml.bak-freecad-mcp`.
- **Tested on:** macOS with FreeCAD 1.1.4 and system Python 3.9, registered with Claude Code,
  Codex and Hermes. **Not tested:** Linux, the automatic FreeCAD install step, older FreeCAD
  versions. Windows is not covered by `install.sh`; the server itself has Windows detection for
  `FreeCADCmd.exe` and can be registered by hand, but that is untested.

## What is in this folder

```
install.sh            one-command installer
uninstall.sh          removes everything the installer added
server/freecad_mcp.py the MCP server (Python standard library only)
server/fc_runner.py   the part that runs inside FreeCAD
skill/freecad/SKILL.md  the skill: how an agent should use the tools
examples/             two complete parts for run_script
tests/smoke_test.py   end-to-end test (MCP protocol + real geometry)
docs/REVIEW.md        review of the first version and what changed
```
