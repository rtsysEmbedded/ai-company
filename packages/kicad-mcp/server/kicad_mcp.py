#!/usr/bin/env python3
"""KiCad MCP server.

Speaks MCP (JSON-RPC 2.0, one message per line) on stdin/stdout using only the Python
standard library, so it runs on any Python 3.8+ with nothing to pip install. It drives KiCad
three ways: kicad-cli for checks, exports and renders; KiCad's bundled Python (pcbnew) for
editing boards, through kicad_runner.py; and its own generator for schematics (kicad_sch.py).
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicad_sch  # noqa: E402
from kicad_lib import KiCadError, Libraries  # noqa: E402

SERVER_NAME = "kicad-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kicad_runner.py")
MAX_ISSUES = 100
EXPORTS = ("gerbers", "position", "bom", "netlist", "schematic_pdf", "step")

PROJECT_HELP = (
    "Project name, e.g. 'blinky'. It lives in its own folder in the workdir "
    "(default ~/KiCad_Projects/blinky/blinky.kicad_sch, .kicad_pcb, .kicad_pro). "
    "A path to a project folder or to one of its files also works."
)
POINT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}

TOOLS = [
    {
        "name": "kicad_info",
        "description": "Report the KiCad version, where its libraries are, the workdir and the projects in it. "
        "Call this first to confirm KiCad is reachable.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_library",
        "description": "Find symbols or footprints in KiCad's stock libraries by keywords (all words must match the "
        "name, description or keywords). Returns ids like 'Device:R' or 'Resistor_SMD:R_0603_1608Metric', which is "
        "what create_schematic needs. Never guess an id: search for it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["symbol", "footprint"]},
                "query": {"type": "string", "description": "Keywords, e.g. '555 timer', 'LED', 'R_0603', 'DIP-8', 'PinHeader 1x02'."},
                "limit": {"type": "integer", "description": "Maximum results. Default 25."},
            },
            "required": ["kind", "query"],
        },
    },
    {
        "name": "library_item",
        "description": "Details of one symbol (its pins with number, name and electrical type, units, default footprint) "
        "or one footprint (its pads with positions and sizes). Check a symbol's pin numbers before wiring it, and check "
        "that the footprint's pad numbers match those pin numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["symbol", "footprint"]},
                "id": {"type": "string", "description": "Library id, 'Library:Name'."},
            },
            "required": ["kind", "id"],
        },
    },
    {
        "name": "create_schematic",
        "description": "Write the project's schematic from a full description: components and the nets that connect "
        "their pins. It replaces the whole schematic each time, so always send the complete design. Connections are "
        "made with net labels on every pin (no drawn wires). Before saving, the tool exports KiCad's own netlist and "
        "checks it against 'nets'; it refuses to save a schematic that does not match. "
        "Every pin must be in a net or in 'no_connect'. To satisfy ERC, add a 'power:PWR_FLAG' component (ref "
        "'#FLG01', pin '1') to each power net that is fed from a connector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "components": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string", "description": "Reference designator, e.g. R1, U1, J1."},
                            "symbol": {"type": "string", "description": "Symbol id from search_library, e.g. 'Device:R'."},
                            "value": {"type": "string", "description": "e.g. '10k', '100n', 'NE555'. Default: the symbol's name."},
                            "footprint": {"type": "string", "description": "Footprint id from search_library. Needed for the PCB."},
                            "rotation": {"type": "integer", "enum": [0, 90, 180, 270], "description": "Counter-clockwise. Default 0."},
                            "at": dict(POINT, description="Optional [x, y] on the sheet in mm. Omit for automatic placement (give it for all components or none)."),
                            "fields": {"type": "object", "description": "Extra fields, e.g. {\"MPN\": \"RC0603FR-0710KL\"}.", "additionalProperties": {"type": "string"}},
                        },
                        "required": ["ref", "symbol"],
                    },
                },
                "nets": {
                    "type": "object",
                    "description": "Net name -> list of pins written 'REF.PIN', e.g. {\"VCC\": [\"U1.8\", \"R1.1\"]}. "
                    "PIN is the pin number, or a pin name such as 'U1.GND' (which selects every pin with that name).",
                    "additionalProperties": {"type": "array", "items": {"type": "string"}},
                },
                "no_connect": {"type": "array", "items": {"type": "string"}, "description": "Pins deliberately left unconnected, 'REF.PIN'."},
                "title": {"type": "string"},
                "rev": {"type": "string"},
                "company": {"type": "string"},
            },
            "required": ["project", "components", "nets"],
        },
    },
    {
        "name": "run_erc",
        "description": "Run KiCad's Electrical Rules Check on the project's schematic and return the errors and warnings.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": PROJECT_HELP}}, "required": ["project"]},
    },
    {
        "name": "update_pcb_from_schematic",
        "description": "Bring the board in line with the schematic: add footprints for new components (parked in a row "
        "for you to place), swap changed footprints in place, remove footprints whose component is gone, and assign "
        "every pad its net. Creates the board if there is none. Run it after every create_schematic.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "remove_extra": {"type": "boolean", "description": "Remove footprints that are not in the schematic. Default true."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "edit_board",
        "description": "Change the board: set the outline, place footprints, add tracks, vias, copper zones and "
        "silkscreen text, or clear existing ones. All coordinates are mm with Y pointing down. Net names may be "
        "given without the leading '/'. Use inspect_board for pad positions before routing, and run_drc afterwards.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "outline": {
                    "type": "object",
                    "description": "Board edge; replaces the existing outline. Either {\"rect\": [x1, y1, x2, y2]} or {\"polygon\": [[x, y], ...]}.",
                    "properties": {"rect": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                                   "polygon": {"type": "array", "items": POINT}},
                },
                "place": {
                    "type": "array",
                    "description": "Move footprints. x/y is the footprint origin (usually pad 1 or the centre; see inspect_board).",
                    "items": {
                        "type": "object",
                        "properties": {"ref": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
                                       "rotation": {"type": "number", "description": "Degrees, counter-clockwise."},
                                       "side": {"type": "string", "enum": ["top", "bottom"]}},
                        "required": ["ref"],
                    },
                },
                "tracks": {
                    "type": "array",
                    "description": "Each track is a polyline on one layer. Start and end exactly on pad centres or other track ends.",
                    "items": {
                        "type": "object",
                        "properties": {"net": {"type": "string"}, "points": {"type": "array", "items": POINT, "minItems": 2},
                                       "width": {"type": "number", "description": "mm. Default 0.25; use 0.5 or more for power."},
                                       "layer": {"type": "string", "description": "F.Cu (default) or B.Cu."}},
                        "required": ["net", "points"],
                    },
                },
                "vias": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"net": {"type": "string"}, "at": POINT,
                                       "size": {"type": "number", "description": "Pad diameter, mm. Default 0.8."},
                                       "drill": {"type": "number", "description": "mm. Default 0.4."}},
                        "required": ["net", "at"],
                    },
                },
                "zones": {
                    "type": "array",
                    "description": "Copper pours, e.g. a GND plane on B.Cu. Without 'polygon' the zone covers the whole board outline. Zones are filled automatically.",
                    "items": {
                        "type": "object",
                        "properties": {"net": {"type": "string"}, "layer": {"type": "string", "description": "Default B.Cu."},
                                       "polygon": {"type": "array", "items": POINT},
                                       "clearance": {"type": "number", "description": "mm."}},
                        "required": ["net"],
                    },
                },
                "texts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}, "at": POINT,
                                       "layer": {"type": "string", "description": "Default F.SilkS."},
                                       "size": {"type": "number", "description": "Text height in mm. Default 1.0."}},
                        "required": ["text", "at"],
                    },
                },
                "clear": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["tracks", "zones", "outline", "texts"]},
                    "description": "Remove all of these first ('tracks' also removes vias). Applied before anything is added.",
                },
                "clear_nets": {"type": "array", "items": {"type": "string"}, "description": "Remove the tracks and vias of just these nets."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "inspect_board",
        "description": "Describe the board: outline, every footprint with position, rotation, side and pads (pad centre "
        "and net), nets, tracks, vias, zones and the number of connections still unrouted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "include_pads": {"type": "boolean", "description": "List each footprint's pads. Default true."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "run_drc",
        "description": "Run KiCad's Design Rules Check on the board (refilling zones first) and return violations, "
        "unconnected items and differences from the schematic. A finished board has zero errors and zero unconnected items.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": PROJECT_HELP}}, "required": ["project"]},
    },
    {
        "name": "render",
        "description": "Make a picture to look at: the board in 3D from the top or bottom (PNG), or the schematic "
        "(PNG where a converter exists, otherwise PDF). Returns the file path; open it with your image viewer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "view": {"type": "string", "enum": ["board_top", "board_bottom", "schematic"]},
            },
            "required": ["project", "view"],
        },
    },
    {
        "name": "export",
        "description": "Write manufacturing and documentation files into the project's 'output' folder. "
        "'gerbers' also writes the drill files and a zip ready for a board house.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_HELP},
                "outputs": {"type": "array", "items": {"type": "string", "enum": list(EXPORTS)}, "minItems": 1},
            },
            "required": ["project", "outputs"],
        },
    },
    {
        "name": "run_script",
        "description": "Run Python inside KiCad's own interpreter for board work the other tools cannot do. Available "
        "names: pcbnew, board (the project's board, when 'project' is given), mm(x) -> internal units, to_mm(v), "
        "xy(x, y) -> a point in mm, find_net(name), WORKDIR. The board is saved afterwards unless the script raises. "
        "print() output is returned; assign 'result' to return a value. Remove items with board.Delete(item), never "
        "board.Remove(item). Do not fill zones in a script; they are refilled automatically after it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
                "project": {"type": "string", "description": PROJECT_HELP + " Optional."},
                "save": {"type": "boolean", "description": "Save the board after the script. Default true."},
            },
            "required": ["code"],
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


def log(message):
    sys.stderr.write("[kicad-mcp] %s\n" % message)
    sys.stderr.flush()


def workdir():
    return os.path.abspath(os.path.expanduser(os.environ.get("KICAD_MCP_WORKDIR") or "~/KiCad_Projects"))


def timeout():
    return float(os.environ.get("KICAD_MCP_TIMEOUT") or 180)


# --- locating KiCad -----------------------------------------------------------
_found = {}


def find_cli():
    if "cli" in _found:
        return _found["cli"]
    override = os.environ.get("KICAD_CLI")
    if override:
        argv = shlex.split(override)
        if not (os.path.isfile(argv[0]) or shutil.which(argv[0])):
            raise KiCadError("KICAD_CLI is set to %r, which does not exist." % argv[0])
    else:
        candidates = [shutil.which("kicad-cli")]
        for pattern in ("/Applications/KiCad*/KiCad.app", "/Applications/KiCad.app", "~/Applications/KiCad*/KiCad.app", "~/Applications/KiCad.app"):
            for app in sorted(glob.glob(os.path.expanduser(pattern)), reverse=True):
                candidates.append(os.path.join(app, "Contents", "MacOS", "kicad-cli"))
        if os.environ.get("ProgramFiles"):
            candidates.extend(sorted(glob.glob(os.path.join(os.environ["ProgramFiles"], "KiCad", "*", "bin", "kicad-cli.exe")), reverse=True))
        argv = next(([c] for c in candidates if c and os.path.isfile(c)), None)
        if argv is None:
            raise KiCadError(
                "KiCad was not found. Install it from https://www.kicad.org/download/, or set the KICAD_CLI "
                "environment variable to the full path of kicad-cli."
            )
    _found["cli"] = argv
    return argv


def _install_root():
    """Directory containing kicad-cli, with symlinks resolved (so bundle-relative paths work)."""
    return os.path.dirname(os.path.realpath(find_cli()[0]))


def find_python():
    """The Python interpreter that can `import pcbnew`."""
    if "python" in _found:
        return _found["python"]
    override = os.environ.get("KICAD_PYTHON")
    root = _install_root()
    bundled = [
        os.path.join(root, "..", "Frameworks", "Python.framework", "Versions", "Current", "bin", "python3"),
        os.path.join(root, "python.exe"),
    ]
    candidates = [override] if override else [c for c in bundled if os.path.isfile(c)]
    if not candidates:
        candidates = sorted({c for c in (shutil.which("python3"), "/usr/bin/python3") if c})
    for candidate in candidates:
        try:
            probe = subprocess.run([candidate, "-c", "import pcbnew"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            _found["python"] = os.path.normpath(candidate)
            return _found["python"]
    raise KiCadError(
        "Found kicad-cli but no Python that can 'import pcbnew' (tried: %s). Set the KICAD_PYTHON environment "
        "variable to KiCad's Python interpreter." % ", ".join(candidates)
    )


def library_dirs(kind):
    """kind is 'symbols' or 'footprints'."""
    env_name = "KICAD_SYMBOL_DIR" if kind == "symbols" else "KICAD_FOOTPRINT_DIR"
    dirs = []
    if os.environ.get(env_name):
        dirs.extend(os.environ[env_name].split(os.pathsep))
    pattern = re.compile(r"KICAD\d+_%s_DIR$" % ("SYMBOL" if kind == "symbols" else "FOOTPRINT"))
    dirs.extend(value for key, value in sorted(os.environ.items()) if pattern.match(key))
    root = _install_root()
    dirs.extend([
        os.path.join(root, "..", "SharedSupport", kind),
        os.path.join(root, "..", "share", "kicad", kind),
        "/usr/share/kicad/" + kind,
        "/usr/local/share/kicad/" + kind,
    ])
    seen, result = set(), []
    for directory in dirs:
        directory = os.path.normpath(directory)
        if directory not in seen and os.path.isdir(directory):
            seen.add(directory)
            result.append(directory)
    return result


def libraries():
    if "libs" not in _found:
        symbols, footprints = library_dirs("symbols"), library_dirs("footprints")
        if not symbols or not footprints:
            raise KiCadError(
                "KiCad's %s were not found. Install the KiCad libraries, or set KICAD_SYMBOL_DIR and KICAD_FOOTPRINT_DIR."
                % ("symbol libraries" if not symbols else "footprint libraries")
            )
        cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kicad-mcp")
        _found["libs"] = Libraries(symbols, footprints, cache)
    return _found["libs"]


def run_cli(args, check=True):
    """Run kicad-cli. Returns (exit_code, output) with font-configuration noise removed."""
    try:
        proc = subprocess.run(find_cli() + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout())
    except subprocess.TimeoutExpired:
        raise KiCadError("kicad-cli %s did not finish within %g s (raise KICAD_MCP_TIMEOUT)." % (" ".join(args[:3]), timeout()))
    except OSError as exc:
        raise KiCadError("Could not start kicad-cli: %s" % exc)
    lines = [line for line in proc.stdout.decode("utf-8", "replace").splitlines() if "Fontconfig" not in line]
    output = "\n".join(lines).strip()
    if check and proc.returncode != 0:
        raise KiCadError("kicad-cli %s failed (exit %d):\n%s" % (" ".join(args[:3]), proc.returncode, output[-2000:]))
    return proc.returncode, output


def kicad_version():
    if "version" not in _found:
        _, output = run_cli(["version"])
        _found["version"] = output.split()[-1] if output else "unknown"
    return _found["version"]


def kicad_major():
    match = re.match(r"(\d+)", kicad_version())
    return int(match.group(1)) if match else 0


def run_job(op, args):
    """Run one operation in KiCad's Python via kicad_runner.py."""
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        job_path, result_path = os.path.join(tmp, "job.json"), os.path.join(tmp, "result.json")
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump({"op": op, "args": args, "result_path": result_path}, handle)
        try:
            proc = subprocess.run([find_python(), RUNNER], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=dict(os.environ, KICAD_MCP_JOB=job_path), timeout=timeout())
        except subprocess.TimeoutExpired:
            raise KiCadError("KiCad did not finish within %g s (raise KICAD_MCP_TIMEOUT)." % timeout())
        if not os.path.exists(result_path):
            tail = proc.stdout.decode("utf-8", "replace")[-2000:]
            raise KiCadError("KiCad's Python exited with code %s without a result.\n%s" % (proc.returncode, tail))
        with open(result_path, "r", encoding="utf-8") as handle:
            outcome = json.load(handle)
        if not outcome["ok"] and op != "run_script":
            raise KiCadError(outcome["payload"]["error"])
        return outcome["payload"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- projects -----------------------------------------------------------------
class Project:
    def __init__(self, spec):
        if not spec or not isinstance(spec, str):
            raise KiCadError("'project' is required")
        path = os.path.expanduser(spec)
        stem, ext = os.path.splitext(path)
        if ext in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
            self.dir, self.name = os.path.dirname(os.path.abspath(path)), os.path.basename(stem)
        else:
            if not os.path.isabs(path):
                path = os.path.join(workdir(), path)
            self.dir, self.name = os.path.abspath(path), os.path.basename(os.path.normpath(path))
        if not re.match(r"^[\w.\- ]+$", self.name):
            raise KiCadError("Project names may only contain letters, digits, '_', '-', '.' and spaces; got %r" % self.name)
        base = os.path.join(self.dir, self.name)
        self.sch, self.pcb, self.pro = base + ".kicad_sch", base + ".kicad_pcb", base + ".kicad_pro"
        self.output = os.path.join(self.dir, "output")

    def need_schematic(self):
        if not os.path.isfile(self.sch):
            raise KiCadError("Project %r has no schematic yet (%s). Use create_schematic first." % (self.name, self.sch))

    def need_board(self):
        if not os.path.isfile(self.pcb):
            raise KiCadError("Project %r has no board yet (%s). Use update_pcb_from_schematic first." % (self.name, self.pcb))


def export_netlist(schematic):
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        xml_path = os.path.join(tmp, "netlist.xml")
        run_cli(["sch", "export", "netlist", "--format", "kicadxml", "-o", xml_path, schematic])
        if not os.path.isfile(xml_path):
            raise KiCadError("KiCad could not export a netlist from %s" % schematic)
        return kicad_sch.read_netlist(xml_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def refill_zones(board):
    """Zones can only be filled reliably through kicad-cli."""
    with open(board, "r", encoding="utf-8") as handle:
        if "(zone" not in handle.read():
            return False
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        run_cli(["pcb", "drc", "--refill-zones", "--save-board", "--format", "json", "-o", os.path.join(tmp, "drc.json"), board])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


# --- tools --------------------------------------------------------------------
def tool_kicad_info(args):
    info = run_job("info", {})
    libs = libraries()
    projects = []
    if os.path.isdir(workdir()):
        for name in sorted(os.listdir(workdir())):
            folder = os.path.join(workdir(), name)
            if os.path.isdir(folder) and any(f.endswith((".kicad_pro", ".kicad_sch", ".kicad_pcb")) for f in os.listdir(folder)):
                projects.append(name)
    return {
        "kicad_version": kicad_version(),
        "kicad_cli": " ".join(find_cli()),
        "kicad_python": find_python(),
        "pcbnew_version": info["kicad_version"],
        "symbol_dirs": libs.symbol_dirs,
        "footprint_dirs": libs.footprint_dirs,
        "workdir": workdir(),
        "projects": projects,
        "server_version": SERVER_VERSION,
    }


def tool_search_library(args):
    return libraries().search(args["kind"], args["query"], int(args.get("limit") or 25))


def tool_library_item(args):
    if args["kind"] == "symbol":
        return libraries().describe_symbol(args["id"])
    if args["kind"] == "footprint":
        return libraries().describe_footprint(args["id"])
    raise KiCadError("kind must be 'symbol' or 'footprint'")


def tool_create_schematic(args):
    project = Project(args["project"])
    root_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "kicad-mcp:%s:root" % project.name))
    if os.path.isfile(project.sch):
        with open(project.sch, "r", encoding="utf-8") as handle:
            match = re.search(r'\(uuid\s+"?([0-9a-fA-F-]{36})"?\s*\)', handle.read(4000))
        if match:
            root_uuid = match.group(1)
    text, report = kicad_sch.build_schematic(args, libraries(), project.name, root_uuid, kicad_major())
    expected = report.pop("expected")

    # Build and verify in a scratch folder; the project is only touched once KiCad agrees with the request.
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        candidate = os.path.join(tmp, project.name + ".kicad_sch")
        with open(candidate, "w", encoding="utf-8") as handle:
            handle.write(text)
        if kicad_major() >= 9:
            run_cli(["sch", "upgrade", "--force", candidate], check=False)  # rewrite in KiCad's native format
        problems, names = kicad_sch.check_netlist(export_netlist(candidate), expected)
        if problems:
            raise KiCadError(
                "The generated schematic did not match the requested nets, so nothing was saved. This is a bug in "
                "the generator, not in your request. Mismatches:\n- " + "\n- ".join(problems)
            )
        os.makedirs(project.dir, exist_ok=True)
        shutil.copyfile(candidate, project.sch)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not os.path.isfile(project.pro) and not os.path.isfile(project.pcb):
        run_job("run_script", {"board": project.pcb, "code": "", "workdir": project.dir})  # KiCad writes the project file with the empty board
    report.update({
        "schematic": project.sch,
        "verified": "KiCad's exported netlist matches the requested nets",
        "net_names_in_kicad": names,
        "next": "Run run_erc, then update_pcb_from_schematic.",
    })
    return report


def _issue(item):
    entry = {"severity": item.get("severity"), "type": item.get("type"), "description": item.get("description"),
             "items": [i.get("description") for i in item.get("items", [])]}
    positions = [[i["pos"]["x"], i["pos"]["y"]] for i in item.get("items", []) if "pos" in i]
    if positions:
        entry["at"] = positions
    return entry


def _condense(issues):
    """Errors first, identical findings merged with a count, so one noisy rule cannot bury the rest."""
    merged = {}
    for issue in issues:
        key = (issue["severity"], issue["type"], issue["description"], tuple(sorted(issue["items"])))
        if key in merged:
            merged[key]["count"] = merged[key].get("count", 1) + 1
        else:
            merged[key] = issue
    ordered = sorted(merged.values(), key=lambda i: (i["severity"] != "error", i["type"]))
    by_type = {}
    for issue in issues:
        label = "%s: %s" % (issue["severity"], issue["type"])
        by_type[label] = by_type.get(label, 0) + 1
    return ordered[:MAX_ISSUES], by_type


def tool_run_erc(args):
    project = Project(args["project"])
    project.need_schematic()
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        report_path = os.path.join(tmp, "erc.json")
        run_cli(["sch", "erc", "--format", "json", "--severity-all", "-o", report_path, project.sch])
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    issues = [_issue(v) for sheet in report.get("sheets", []) for v in sheet.get("violations", [])]
    for issue in issues:
        issue.pop("at", None)  # ERC positions are not in sheet millimetres; the item text identifies the pin
    errors = sum(1 for i in issues if i["severity"] == "error")
    listed, by_type = _condense(issues)
    return {"schematic": project.sch, "errors": errors, "warnings": len(issues) - errors, "clean": not issues,
            "by_type": by_type, "violations": listed}


def tool_update_pcb(args):
    project = Project(args["project"])
    project.need_schematic()
    netlist = export_netlist(project.sch)
    libs = libraries()
    footprint_dirs = {}
    for comp in netlist["components"]:
        if comp["footprint"]:
            try:
                libs.footprint_path(comp["footprint"])
            except KiCadError as exc:
                raise KiCadError("%s: %s" % (comp["ref"], exc))
            lib = comp["footprint"].split(":", 1)[0]
            footprint_dirs[lib] = libs.footprint_lib_dir(lib)
    result = run_job("sync", {"board": project.pcb, "components": netlist["components"], "nets": netlist["nets"],
                              "footprint_dirs": footprint_dirs, "remove_extra": args.get("remove_extra", True)})
    refill_zones(project.pcb)
    return result


def tool_edit_board(args):
    project = Project(args["project"])
    project.need_board()
    job = {key: value for key, value in args.items() if key != "project"}
    job["board"] = project.pcb
    result = run_job("edit", job)
    if result.pop("has_zones", False):
        result["zones_refilled"] = refill_zones(project.pcb)
    return result


def tool_inspect_board(args):
    project = Project(args["project"])
    project.need_board()
    return run_job("inspect", {"board": project.pcb, "include_pads": args.get("include_pads", True)})


def tool_run_drc(args):
    project = Project(args["project"])
    project.need_board()
    tmp = tempfile.mkdtemp(prefix="kicad-mcp-")
    try:
        report_path = os.path.join(tmp, "drc.json")
        command = ["pcb", "drc", "--format", "json", "--severity-all", "--refill-zones", "--save-board", "-o", report_path]
        if os.path.isfile(project.sch):
            command.append("--schematic-parity")
        run_cli(command + [project.pcb])
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    violations = [_issue(v) for v in report.get("violations", [])]
    unconnected = [_issue(v) for v in report.get("unconnected_items", [])]
    parity = [_issue(v) for v in report.get("schematic_parity", [])]
    errors = sum(1 for v in violations if v["severity"] == "error")
    listed, by_type = _condense(violations)
    return {
        "board": project.pcb,
        "errors": errors,
        "warnings": len(violations) - errors,
        "unconnected": len(unconnected),
        "schematic_differences": len(parity),
        "clean": not violations and not unconnected and not parity,
        "by_type": by_type,
        "violations": listed,
        "unconnected_items": unconnected[:MAX_ISSUES],
        "schematic_parity": _condense(parity)[0],
    }


def _pdf_to_png(pdf, png):
    """Rasterise the first page of a PDF with whatever the system offers. Returns True on success."""
    try:
        if sys.platform == "darwin" and shutil.which("qlmanage"):
            folder = os.path.dirname(png)
            subprocess.run(["qlmanage", "-t", "-s", "2600", "-o", folder, pdf], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
            produced = os.path.join(folder, os.path.basename(pdf) + ".png")
            if os.path.isfile(produced):
                os.replace(produced, png)
                return True
        if shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile", pdf, png[:-4]], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            return os.path.isfile(png)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def tool_render(args):
    project = Project(args["project"])
    view = args["view"]
    os.makedirs(project.output, exist_ok=True)
    if view in ("board_top", "board_bottom"):
        project.need_board()
        image = os.path.join(project.output, "%s_%s.png" % (project.name, view))
        run_cli(["pcb", "render", "-o", image, "--side", view.split("_")[1], "-w", "1600", "-h", "1200", project.pcb])
        if not os.path.isfile(image):
            raise KiCadError("KiCad did not produce %s" % image)
        return {"image": image, "view": view}
    if view == "schematic":
        project.need_schematic()
        pdf = os.path.join(project.output, project.name + "_schematic.pdf")
        run_cli(["sch", "export", "pdf", "-o", pdf, project.sch])
        png = pdf[:-4] + ".png"
        if _pdf_to_png(pdf, png):
            return {"image": png, "pdf": pdf, "view": view}
        return {"pdf": pdf, "view": view, "note": "No PDF-to-PNG converter found (qlmanage on macOS, pdftoppm on Linux); open the PDF instead."}
    raise KiCadError("view must be board_top, board_bottom or schematic")


def tool_export(args):
    project = Project(args["project"])
    outputs = args.get("outputs") or []
    unknown = [o for o in outputs if o not in EXPORTS]
    if unknown or not outputs:
        raise KiCadError("'outputs' must list one or more of: %s" % ", ".join(EXPORTS))
    os.makedirs(project.output, exist_ok=True)
    base = os.path.join(project.output, project.name)
    files = {}
    for output in outputs:
        if output in ("gerbers", "position", "step"):
            project.need_board()
        else:
            project.need_schematic()
        if output == "gerbers":
            folder = os.path.join(project.output, "gerbers")
            shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder)
            with open(project.pcb, "r", encoding="utf-8") as handle:
                inner = sorted(set(re.findall(r'"(In\d+\.Cu)"', handle.read(20000))), key=lambda n: int(n[2:-3]))
            layers = ["F.Cu"] + inner + ["B.Cu", "F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"]
            run_cli(["pcb", "export", "gerbers", "--check-zones", "-l", ",".join(layers), "-o", folder + os.sep, project.pcb])
            run_cli(["pcb", "export", "drill", "-o", folder + os.sep, project.pcb])
            names = sorted(os.listdir(folder))
            if not names:
                raise KiCadError("KiCad wrote no Gerber files")
            archive = base + "-gerbers.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name in names:
                    bundle.write(os.path.join(folder, name), name)
            files["gerbers"] = {"folder": folder, "files": names, "zip": archive}
        elif output == "position":
            path = base + "-pos.csv"
            run_cli(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", path, project.pcb])
            files["position"] = path
        elif output == "step":
            path = base + ".step"
            run_cli(["pcb", "export", "step", "-f", "-o", path, project.pcb], check=False)
            files["step"] = path
        elif output == "bom":
            path = base + "-bom.csv"
            run_cli(["sch", "export", "bom", "--fields", "Reference,Value,Footprint,${QUANTITY}", "--labels", "Refs,Value,Footprint,Qty",
                     "--group-by", "Value,Footprint", "-o", path, project.sch])
            files["bom"] = path
        elif output == "netlist":
            path = base + ".net"
            run_cli(["sch", "export", "netlist", "-o", path, project.sch])
            files["netlist"] = path
        elif output == "schematic_pdf":
            path = base + "_schematic.pdf"
            run_cli(["sch", "export", "pdf", "-o", path, project.sch])
            files["schematic_pdf"] = path
    missing = [name for name, value in files.items() if isinstance(value, str) and not os.path.isfile(value)]
    if missing:
        raise KiCadError("KiCad did not produce: %s" % ", ".join(missing))
    return {"output_folder": project.output, "files": files}


def tool_run_script(args):
    job = {"code": args["code"], "save": args.get("save", True), "workdir": workdir()}
    project = None
    if args.get("project"):
        project = Project(args["project"])
        job["board"] = project.pcb
        job["workdir"] = project.dir
    os.makedirs(job["workdir"], exist_ok=True)
    result = run_job("run_script", job)
    if result.pop("has_zones", False):
        result["zones_refilled"] = refill_zones(project.pcb)
    return result


HANDLERS = {
    "kicad_info": tool_kicad_info,
    "search_library": tool_search_library,
    "library_item": tool_library_item,
    "create_schematic": tool_create_schematic,
    "run_erc": tool_run_erc,
    "update_pcb_from_schematic": tool_update_pcb,
    "edit_board": tool_edit_board,
    "inspect_board": tool_inspect_board,
    "run_drc": tool_run_drc,
    "render": tool_render,
    "export": tool_export,
    "run_script": tool_run_script,
}


def call_tool(name, args):
    """Return (ok, payload)."""
    try:
        payload = HANDLERS[name](args)
    except KiCadError as exc:
        return False, str(exc)
    except KeyError as exc:
        return False, "Missing required argument: %s" % exc
    return "error" not in payload, payload


def tool_result(ok, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": not ok}


def handle(message):
    """Return the JSON-RPC response for a message, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None or method is None:
        return None
    params = message.get("params") or {}

    def reply(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def fail(code, text):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return reply(
            {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Design electronics with KiCad: search_library -> create_schematic -> run_erc -> "
                "update_pcb_from_schematic -> edit_board -> run_drc -> render -> export. Units are millimetres.",
            }
        )
    if method == "ping":
        return reply({})
    if method == "tools/list":
        return reply({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return fail(-32602, "Unknown tool: %s" % name)
        try:
            ok, payload = call_tool(name, params.get("arguments") or {})
        except Exception as exc:
            log("tool %s crashed: %r" % (name, exc))
            ok, payload = False, "Internal error: %r" % (exc,)
        return reply(tool_result(ok, payload))
    return fail(-32601, "Method not found: %s" % method)


def serve():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for raw in iter(stdin.readline, b""):
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if not isinstance(message, dict):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            else:
                response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response).encode("utf-8") + b"\n")
            stdout.flush()


def check():
    """Print what the server found and whether KiCad answers. Exit code 0 means usable."""
    ok, payload = call_tool("kicad_info", {})
    print(payload if isinstance(payload, str) else json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(SERVER_VERSION)
    elif "--check" in sys.argv:
        sys.exit(check())
    else:
        serve()
