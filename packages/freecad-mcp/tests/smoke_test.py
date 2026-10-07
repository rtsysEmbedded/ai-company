#!/usr/bin/env python3
"""End-to-end test: talks MCP to the server over stdio and checks the geometry FreeCAD produces.

Usage: python3 tests/smoke_test.py [path/to/freecad_mcp.py]
Works in a temporary directory, so it never touches your models. Exit code 0 means every check passed.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "server", "freecad_mcp.py")
EXAMPLES = os.path.join(HERE, "..", "examples")

failures = []


def check(label, condition, detail=""):
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", label, "" if condition else "  -> %s" % detail))
    if not condition:
        failures.append(label)


class Client:
    def __init__(self, workdir):
        env = dict(os.environ, FREECAD_MCP_WORKDIR=workdir)
        self.proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.next_id = 0

    def send(self, payload):
        self.proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        response = json.loads(self.proc.stdout.readline().decode("utf-8"))
        assert response.get("id") == self.next_id, "response id mismatch: %r" % (response,)
        return response

    def tool(self, tool_name, **arguments):
        """Return (ok, payload) where payload is the decoded JSON text of the tool result."""
        result = self.request("tools/call", {"name": tool_name, "arguments": arguments})["result"]
        text = result["content"][0]["text"]
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return not result["isError"], payload

    def close(self):
        self.proc.stdin.close()
        return self.proc.wait(timeout=10)


def find(objects, name):
    return next((o for o in objects if o["name"] == name), None)


def main():
    workdir = tempfile.mkdtemp(prefix="freecad-mcp-test-")
    client = Client(workdir)
    try:
        print("Protocol")
        init = client.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-test", "version": "1"}},
        )["result"]
        check("initialize negotiates the requested version", init["protocolVersion"] == "2025-06-18", init)
        check("server advertises tools", "tools" in init["capabilities"], init)
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        check("ping answers after a notification (notifications get no reply)", client.request("ping").get("result") == {})
        tools = client.request("tools/list")["result"]["tools"]
        names = sorted(t["name"] for t in tools)
        expected = ["boolean", "create_primitive", "export", "freecad_info", "inspect_document", "run_script"]
        check("tools/list returns the 6 tools", names == expected, names)
        check("every tool has an object input schema", all(t["inputSchema"]["type"] == "object" for t in tools))
        check("unknown method is a JSON-RPC error", client.request("nope/nothing").get("error", {}).get("code") == -32601)
        check("unknown tool is a JSON-RPC error", "error" in client.request("tools/call", {"name": "nope", "arguments": {}}))

        print("FreeCAD")
        ok, info = client.tool("freecad_info")
        check("freecad_info reaches FreeCAD", ok and "freecad_version" in info, info)
        if not ok:
            return
        print("        FreeCAD %s via %s" % (info["freecad_version"], info["freecad_command"]))

        print("Bracket from primitives and booleans")
        ok, base = client.tool("create_primitive", document="bracket", shape="box", name="Base", length=80, width=80, height=15)
        check("create base box (creates the document)", ok and base["created"]["volume_mm3"] == 96000, base)
        ok, wall = client.tool("create_primitive", document="bracket", shape="box", name="Wall", length=15, width=80, height=100)
        check("create wall box", ok and wall["created"]["volume_mm3"] == 120000, wall)
        for i, y in enumerate((20, 60)):
            ok, hole = client.tool(
                "create_primitive", document="bracket", shape="cylinder", name="Hole%d" % i, radius=4, height=15, position=[50, y, 0]
            )
            check("create hole %d at y=%d" % (i, y), ok and hole["created"]["bbox_min"][:2] == [46, y - 4], hole)
        ok, body = client.tool("boolean", document="bracket", operation="fuse", base="Base", tools=["Wall"], name="Body")
        check("fuse base and wall", ok and abs(body["created"]["volume_mm3"] - 198000) < 0.01, body)
        ok, cut = client.tool("boolean", document="bracket", operation="cut", base="Body", tools=["Hole0", "Hole1"], name="Bracket")
        want = 198000 - 2 * math.pi * 4 ** 2 * 15
        check("cut both holes: volume matches the hand calculation (%.2f)" % want, ok and abs(cut["created"]["volume_mm3"] - want) < 0.01, cut)
        ok, doc = client.tool("inspect_document", document="bracket")
        final = find(doc["objects"], "Bracket") if ok else None
        check("inspect: bracket is a valid 80 x 80 x 100 solid", bool(final) and final["valid"] and final["size"] == [80, 80, 100], final)
        check("inspect: document was saved to disk", os.path.isfile(os.path.join(workdir, "bracket.FCStd")))

        print("Export")
        for ext in ("step", "stl"):
            ok, out = client.tool("export", document="bracket", output="bracket." + ext)
            path = os.path.join(workdir, "bracket." + ext)
            check("export %s writes only the final result" % ext, ok and out["exported"] == ["Bracket"] and os.path.getsize(path) > 500, out)
        ok, out = client.tool("export", document="bracket", output="bracket.xyz")
        check("unsupported export format is reported as a tool error", not ok and "Unsupported" in str(out), out)

        print("run_script")
        ok, out = client.tool(
            "run_script", document="scripted", code="b = Part.makeBox(2, 3, 4)\ndoc.addObject('Part::Feature', 'B').Shape = b\nprint('hi')\nresult = b.Volume"
        )
        check("script output, result value and saved document come back", ok and out["stdout"] == "hi\n" and abs(out["result"] - 24) < 1e-6 and out["saved"], out)
        ok, out = client.tool("run_script", document="scripted", code="doc.addObject('Part::Feature', 'Junk')\nraise ValueError('boom')")
        check("a failing script is a tool error with the traceback", not ok and "ValueError: boom" in out["error"], out)
        ok, doc = client.tool("inspect_document", document="scripted")
        check("a failing script does not save its changes", ok and [o["name"] for o in doc["objects"]] == ["B"], doc)
        ok, out = client.tool("boolean", document="scripted", operation="cut", base="B", tools=["Missing"])
        check("naming a missing object lists the available ones", not ok and "Available: B" in str(out), out)
        ok, out = client.tool("inspect_document", document="does-not-exist")
        check("a missing document is a tool error", not ok and "not found" in str(out), out)

        print("Examples")
        expected_volume = {
            # base + wall above the base, minus four 8 mm holes through 15 mm of material
            "bracket.py": 80 * 80 * 15 + 15 * 80 * 85 - 4 * math.pi * 4 ** 2 * 15,
            # tube + two flange rings outside the tube, minus the bore and four bolt holes per flange
            "flanged_connector.py": math.pi * (15 ** 2 * 60 + 2 * (30 ** 2 - 15 ** 2) * 5 - 12 ** 2 * 60 - 8 * 2.5 ** 2 * 5),
        }
        for filename, want in sorted(expected_volume.items()):
            with open(os.path.join(EXAMPLES, filename), "r", encoding="utf-8") as handle:
                ok, out = client.tool("run_script", document="example-" + filename[:-3], code=handle.read())
            obj = out["objects"][0] if ok else {}
            check(
                "%s builds a valid solid of the expected volume (%.1f)" % (filename, want),
                ok and obj["valid"] and obj["shape_type"] == "Solid" and abs(obj["volume_mm3"] - want) < 0.5,
                out,
            )
    finally:
        code = client.close()
        check("server exits cleanly when stdin closes", code == 0, code)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    print("\n%s" % ("FAILED: %d check(s)" % len(failures) if failures else "All checks passed."))
    sys.exit(1 if failures else 0)
