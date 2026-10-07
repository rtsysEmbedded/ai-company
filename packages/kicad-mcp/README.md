# KiCad MCP

Lets an AI coding agent design electronics in KiCad: schematic, PCB layout, rule checks,
renders and manufacturing files. It is an MCP server plus a skill, and the same install works
with **Claude Code**, **OpenAI Codex** and **Hermes Agent**.

You ask for a circuit ("a 555 LED blinker on a through-hole board, 5-12 V in"), the agent
finds the parts in KiCad's libraries, writes the schematic, lays out and routes the board,
runs KiCad's own ERC and DRC until they pass, and exports Gerbers. You get a normal KiCad
project you can open and keep editing.

## Install on a new machine

Copy this folder to the machine and run:

```bash
./install.sh
```

That is the whole install. The script:

1. finds Python 3.8+ (the server needs nothing else from Python, no `pip install`),
2. copies the server to `~/.local/share/kicad-mcp/`,
3. finds KiCad, and offers to install it with Homebrew/apt/dnf/pacman/zypper if it is missing,
4. runs an end-to-end test that takes a real design from schematic to Gerbers,
5. registers the server as `kicad` with every agent CLI it finds (`claude`, `codex`, `hermes`),
6. installs the `kicad` skill for each of those agents.

Start a new agent session afterwards so it loads the tools. You can delete or move this folder
after installing; re-run `./install.sh` any time to update or repair.

Options:

| Option | Meaning |
| --- | --- |
| `--clients claude,codex,hermes` | Register only with these (default: all that are installed; `none` to skip) |
| `--install-kicad` | Install KiCad without asking if it is missing |
| `--kicad-cli PATH` | Path to `kicad-cli` if it is somewhere unusual |
| `--workdir DIR` | Where projects are stored (default `~/KiCad_Projects`) |
| `--prefix DIR` | Where the server is installed |
| `--skip-test` | Skip the end-to-end test |
| `--no-skill` | Register the tools only, without the skill |

Requirements: macOS or Linux, Python 3.8+, KiCad 9 or newer with its symbol and footprint libraries.

## Tools and skill

The **tools** are what the agent can do in KiCad. The **skill** (`skill/kicad/SKILL.md`) is a
short guide the agent loads when you ask for a circuit: the workflow, the rules for wiring and
routing, what each ERC/DRC finding means and how to fix it, and what to be honest about. It is
copied to `~/.claude/skills/kicad/`, `~/.codex/skills/kicad/` and `~/.hermes/skills/kicad/`.

| Tool | What it does |
| --- | --- |
| `kicad_info` | KiCad version, library locations, workdir, existing projects. A quick "is it working" check. |
| `search_library` | Find symbols and footprints in KiCad's stock libraries by keywords. |
| `library_item` | A symbol's pins, or a footprint's pads. |
| `create_schematic` | Write the schematic from components and nets. Verified against KiCad's netlist before saving. |
| `run_erc` | KiCad's Electrical Rules Check. |
| `update_pcb_from_schematic` | Put the schematic's footprints and nets on the board; add, swap and remove as needed. |
| `edit_board` | Outline, placement, tracks, vias, copper zones, silkscreen text. |
| `inspect_board` | Footprints, pad positions, nets, tracks, zones, unrouted count. |
| `run_drc` | KiCad's Design Rules Check, including unconnected nets and schematic parity. |
| `render` | 3D picture of the board, or a picture of the schematic. |
| `export` | Gerbers + drill (zipped), BOM, pick-and-place, netlist, schematic PDF, STEP. |
| `run_script` | Run Python in KiCad's own interpreter (pcbnew) for anything else on the board. |

A project named `blinky` is the folder `~/KiCad_Projects/blinky/` holding `blinky.kicad_pro`,
`.kicad_sch` and `.kicad_pcb`; exports go to its `output/` subfolder. Units are millimetres.

## Try it

Ask the agent, for example:

- "Use kicad to design a 555 LED blinker on a small through-hole board, powered from a 2-pin header, and export Gerbers."
- "Run ERC and DRC on the blinky project and tell me what is wrong."
- "Render the top of the blinky board and show me."

`examples/` contains that blinker as the exact tool arguments: `blinky.json` for
`create_schematic` and `blinky_board.json` for `edit_board`. The end-to-end test builds it and
checks that ERC and DRC are clean.

## Registering by hand

The installer does this for you. To do it manually, the server is started with
`python3 ~/.local/share/kicad-mcp/kicad_mcp.py` (use full paths).

Claude Code:

```bash
claude mcp add --scope user kicad -- /usr/bin/python3 ~/.local/share/kicad-mcp/kicad_mcp.py
```

Codex, either `codex mcp add kicad -- /usr/bin/python3 ~/.local/share/kicad-mcp/kicad_mcp.py`
or in `~/.codex/config.toml`:

```toml
[mcp_servers.kicad]
command = "/usr/bin/python3"
args = ["/home/you/.local/share/kicad-mcp/kicad_mcp.py"]
```

Hermes, either `hermes mcp add kicad --command /usr/bin/python3 --args ~/.local/share/kicad-mcp/kicad_mcp.py`
or in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  kicad:
    command: /usr/bin/python3
    args:
      - /home/you/.local/share/kicad-mcp/kicad_mcp.py
    enabled: true
```

Any other MCP client works the same way: it is a stdio server with that command.

## Settings

Environment variables read by the server (the installer sets the first two from its options):

| Variable | Default | Meaning |
| --- | --- | --- |
| `KICAD_CLI` | auto-detected | Path to `kicad-cli` |
| `KICAD_MCP_WORKDIR` | `~/KiCad_Projects` | Folder that holds the projects |
| `KICAD_PYTHON` | auto-detected | The Python that can `import pcbnew` (KiCad's bundled one on macOS and Windows) |
| `KICAD_SYMBOL_DIR`, `KICAD_FOOTPRINT_DIR` | auto-detected | Library folders, if they are not next to the KiCad install |
| `KICAD_MCP_TIMEOUT` | `180` | Seconds one KiCad call may take |

## Checking and troubleshooting

```bash
python3 ~/.local/share/kicad-mcp/kicad_mcp.py --check   # can the server use KiCad?
python3 tests/smoke_test.py                             # full end-to-end test
claude mcp list        # or: codex mcp get kicad / hermes mcp test kicad
```

- **"KiCad was not found"**: install KiCad, or re-run `./install.sh --kicad-cli /full/path/to/kicad-cli`.
- **"no Python that can import pcbnew"**: on Linux install the distribution's KiCad package (it
  provides the `pcbnew` module for the system Python), or set `KICAD_PYTHON`.
- **Libraries not found**: install KiCad's library packages, or set `KICAD_SYMBOL_DIR` and `KICAD_FOOTPRINT_DIR`.
- **The agent does not show the tools**: start a new session; agents load MCP servers at startup.

## Uninstall

```bash
./uninstall.sh
```

Removes the server, its three registrations and the skill. KiCad and your projects are left alone.

## Good to know

- **`run_script` runs arbitrary Python** on your machine with your permissions. Only connect
  this server to agents you would trust with a terminal.
- **Schematics are drawn with a net label on every pin, not with wires.** They are electrically
  exact (KiCad's netlist is compared with the request before anything is saved) and readable,
  but they do not look hand-drawn. `create_schematic` rewrites the whole sheet each time, so
  manual edits to the schematic are lost the next time the agent regenerates it. Single sheet only.
- **There is no autorouter.** The agent places every track by coordinates and checks it with
  DRC. That works for small and medium boards; dense boards need interactive routing in KiCad.
- **Only KiCad's stock libraries** are searched. Your own libraries are not.
- **ERC and DRC check rules, not intent.** A board can pass both and still be the wrong circuit.
  Review the schematic before ordering boards.
- Close the project in the KiCad application while the agent is editing it, or reload it
  afterwards; KiCad does not notice files changing underneath it.
- The Codex and Hermes CLIs rewrite their whole config file when a server is added (formatting
  changes, content does not). The installer keeps the original once as
  `config.toml.bak-kicad-mcp` / `config.yaml.bak-kicad-mcp`.
- **Tested on:** macOS with KiCad 10.0.4 and system Python 3.9, registered with Claude Code,
  Codex and Hermes. **Not tested:** Linux, Windows (not covered by `install.sh`), KiCad 9 and
  older, the automatic KiCad install step, and opening the results in the KiCad application
  (they were checked with KiCad's command-line tools and renders only).

## What is in this folder

```
install.sh               one-command installer
uninstall.sh             removes everything the installer added
server/kicad_mcp.py      the MCP server (Python standard library only)
server/kicad_sch.py      schematic generator and netlist verification
server/kicad_lib.py      reads KiCad's symbol and footprint libraries
server/kicad_runner.py   the part that runs inside KiCad's Python (board editing)
skill/kicad/SKILL.md     the skill: how an agent should use the tools
examples/                the 555 blinker as tool arguments
tests/smoke_test.py      end-to-end test (MCP protocol + a real design)
```
