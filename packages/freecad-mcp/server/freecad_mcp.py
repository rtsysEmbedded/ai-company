#!/usr/bin/env python3
"""FreeCAD MCP server.

Speaks MCP (JSON-RPC 2.0, one message per line) on stdin/stdout using only the Python
standard library, so it runs on any Python 3.8+ with nothing to pip install. Each tool
call launches a headless FreeCAD (freecadcmd) that runs fc_runner.py and exits; models
persist between calls as .FCStd files.
"""
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

SERVER_NAME = "freecad-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fc_runner.py")

DOCUMENT_HELP = (
    "Path to the .FCStd file. Relative paths are resolved inside the workdir "
    "(default ~/FreeCAD_Components); '.FCStd' is added if there is no extension."
)

TOOLS = [
    {
        "name": "freecad_info",
        "description": "Report the FreeCAD version, its Python version and the workdir where models are stored. "
        "Call this first to confirm FreeCAD is reachable.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_primitive",
        "description": "Add a parametric solid (box, cylinder, sphere, cone or torus) to a FreeCAD document, "
        "creating the document if it does not exist. All lengths are millimetres. "
        "A box starts at 'position' and extends along +X/+Y/+Z; a cylinder or cone stands on 'position' "
        "along +Z; a sphere or torus is centred on 'position'. Returns the object's name, volume and bounding box.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": DOCUMENT_HELP},
                "shape": {"type": "string", "enum": ["box", "cylinder", "sphere", "cone", "torus"]},
                "name": {"type": "string", "description": "Object name. FreeCAD appends digits if it is taken."},
                "length": {"type": "number", "description": "Box size along X."},
                "width": {"type": "number", "description": "Box size along Y."},
                "height": {"type": "number", "description": "Box, cylinder or cone size along Z."},
                "radius": {"type": "number", "description": "Cylinder or sphere radius."},
                "radius1": {"type": "number", "description": "Cone bottom radius, or torus ring radius."},
                "radius2": {"type": "number", "description": "Cone top radius, or torus tube radius."},
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "[x, y, z] in mm. Default [0, 0, 0].",
                },
                "rotation_axis": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Axis to rotate about, e.g. [0, 1, 0]. Default [0, 0, 1].",
                },
                "rotation_angle": {"type": "number", "description": "Rotation in degrees. Default 0."},
            },
            "required": ["document", "shape"],
        },
    },
    {
        "name": "boolean",
        "description": "Combine objects in a document: 'fuse' (union), 'cut' (subtract every tool from base) "
        "or 'common' (intersection). The inputs stay in the document as hidden children of the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": DOCUMENT_HELP},
                "operation": {"type": "string", "enum": ["fuse", "cut", "common"]},
                "base": {"type": "string", "description": "Name of the object to start from."},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Names of the objects to add to, subtract from or intersect with the base.",
                },
                "name": {"type": "string", "description": "Name for the resulting object."},
            },
            "required": ["document", "operation", "base", "tools"],
        },
    },
    {
        "name": "inspect_document",
        "description": "List every object in a FreeCAD document with its type, validity, volume, surface area "
        "and bounding box. Use it to verify geometry after building it.",
        "inputSchema": {
            "type": "object",
            "properties": {"document": {"type": "string", "description": DOCUMENT_HELP}},
            "required": ["document"],
        },
    },
    {
        "name": "export",
        "description": "Export a document's geometry to STEP, STL, IGES or BREP; the format comes from the output "
        "file extension. By default exports the final results (objects no other object depends on).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": DOCUMENT_HELP},
                "output": {
                    "type": "string",
                    "description": "Output file ending in .step, .stl, .iges or .brep. Relative paths go in the workdir.",
                },
                "objects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Object names to export. Default: the final results.",
                },
                "linear_deflection": {
                    "type": "number",
                    "description": "STL only: maximum mesh deviation in mm. Default 0.1; smaller is finer.",
                },
            },
            "required": ["document", "output"],
        },
    },
    {
        "name": "run_script",
        "description": "Run Python inside FreeCAD for anything the other tools cannot do (sketches, extrusions, "
        "fillets, chamfers, patterns, PartDesign, ...). Available names: FreeCAD, App, Part, Vector, WORKDIR, "
        "and 'doc' when 'document' is given (opened, or created if missing). The document is recomputed and "
        "saved afterwards unless the script raises. print() output is returned; assign to a variable named "
        "'result' to return a value. Each call is a fresh FreeCAD process, so nothing but saved files carries over. "
        "There is no GUI: FreeCADGui and view objects are unavailable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
                "document": {"type": "string", "description": DOCUMENT_HELP + " Optional."},
                "save": {"type": "boolean", "description": "Save the document after the script. Default true."},
            },
            "required": ["code"],
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


class ToolError(Exception):
    pass


def log(message):
    sys.stderr.write("[freecad-mcp] %s\n" % message)
    sys.stderr.flush()


def workdir():
    return os.path.abspath(os.path.expanduser(os.environ.get("FREECAD_MCP_WORKDIR") or "~/FreeCAD_Components"))


def resolve(path, default_ext=None):
    path = os.path.expanduser(path)
    if default_ext and not os.path.splitext(path)[1]:
        path += default_ext
    if not os.path.isabs(path):
        path = os.path.join(workdir(), path)
    return os.path.abspath(path)


def _is_cmd_binary(path):
    name = os.path.basename(path).lower()
    return name.startswith("freecadcmd") or name == "freecad.cmd"


_freecad = None


def find_freecad():
    """Return the argv prefix that runs a script in headless FreeCAD."""
    global _freecad
    if _freecad:
        return _freecad
    override = os.environ.get("FREECAD_CMD")
    if override:
        argv = shlex.split(override)
        if not _is_cmd_binary(argv[0]) and "-c" not in argv and "--console" not in argv:
            argv.append("-c")
        _freecad = argv
        return argv

    candidates = [shutil.which(name) for name in ("freecadcmd", "FreeCADCmd", "freecad.cmd")]
    for app in sorted(glob.glob("/Applications/FreeCAD*.app") + glob.glob(os.path.expanduser("~/Applications/FreeCAD*.app"))):
        candidates.append(os.path.join(app, "Contents", "Resources", "bin", "freecadcmd"))
        candidates.append(os.path.join(app, "Contents", "MacOS", "FreeCADCmd"))
    for root in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
        if root:
            candidates.extend(sorted(glob.glob(os.path.join(root, "FreeCAD*", "bin", "FreeCADCmd.exe")), reverse=True))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            _freecad = [candidate]
            return _freecad
    # The GUI binary also works when started in console mode.
    for name in ("freecad", "FreeCAD"):
        gui = shutil.which(name)
        if gui:
            _freecad = [gui, "-c"]
            return _freecad
    raise ToolError(
        "FreeCAD was not found. Install it from https://www.freecad.org/downloads.php, or set the FREECAD_CMD "
        "environment variable to the full path of freecadcmd (FreeCADCmd.exe on Windows)."
    )


def run_job(op, args):
    argv = find_freecad() + [RUNNER]
    timeout = float(os.environ.get("FREECAD_MCP_TIMEOUT") or 120)
    tmp = tempfile.mkdtemp(prefix="freecad-mcp-")
    try:
        job_path = os.path.join(tmp, "job.json")
        result_path = os.path.join(tmp, "result.json")
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump({"op": op, "args": args, "workdir": workdir(), "result_path": result_path}, handle)
        env = dict(os.environ, FREECAD_MCP_JOB=job_path)
        try:
            proc = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise ToolError("FreeCAD did not finish within %g s (raise FREECAD_MCP_TIMEOUT to allow longer)." % timeout)
        except OSError as exc:
            raise ToolError("Could not start FreeCAD (%s): %s" % (" ".join(argv), exc))
        if not os.path.exists(result_path):
            tail = proc.stdout.decode("utf-8", "replace")[-2000:]
            raise ToolError("FreeCAD exited with code %s without a result.\n%s" % (proc.returncode, tail))
        with open(result_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def call_tool(name, args):
    args = dict(args)
    if args.get("document"):
        args["document"] = resolve(args["document"], ".FCStd")
    if args.get("output"):
        args["output"] = resolve(args["output"])
    outcome = run_job(name, args)
    payload = outcome["payload"]
    if name == "freecad_info" and outcome["ok"]:
        payload["freecad_command"] = " ".join(find_freecad())
        payload["server_version"] = SERVER_VERSION
    return outcome["ok"], payload


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
                "instructions": "Build 3D CAD models with FreeCAD. Units are millimetres. Models are .FCStd files; "
                "verify geometry with inspect_document and use export for STEP or STL.",
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
        except ToolError as exc:
            ok, payload = False, str(exc)
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
    """Print what the server found and whether FreeCAD answers. Exit code 0 means usable."""
    try:
        ok, payload = call_tool("freecad_info", {})
    except ToolError as exc:
        ok, payload = False, str(exc)
    print(payload if isinstance(payload, str) else json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(SERVER_VERSION)
    elif "--check" in sys.argv:
        sys.exit(check())
    else:
        serve()
