#!/usr/bin/env python3
"""End-to-end test: talks MCP to the server over stdio and takes a real design from schematic to Gerbers.

Usage: python3 tests/smoke_test.py [path/to/kicad_mcp.py]
Works in a temporary directory, so it never touches your projects. Exit code 0 means every check passed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "server", "kicad_mcp.py")
EXAMPLES = os.path.join(HERE, "..", "examples")
TOOLS = ["create_schematic", "edit_board", "export", "inspect_board", "kicad_info", "library_item", "render",
         "run_drc", "run_erc", "run_script", "search_library", "update_pcb_from_schematic"]

failures = []


def check(label, condition, detail=""):
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", label, "" if condition else "  -> %s" % (str(detail)[:700],)))
    if not condition:
        failures.append(label)


def load(name):
    with open(os.path.join(EXAMPLES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class Client:
    def __init__(self, workdir):
        env = dict(os.environ, KICAD_MCP_WORKDIR=workdir)
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


def main():
    workdir = tempfile.mkdtemp(prefix="kicad-mcp-test-")
    client = Client(workdir)
    try:
        print("Protocol")
        init = client.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-test", "version": "1"}},
        )["result"]
        check("initialize negotiates the requested version", init["protocolVersion"] == "2025-06-18", init)
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        check("ping answers after a notification (notifications get no reply)", client.request("ping").get("result") == {})
        tools = client.request("tools/list")["result"]["tools"]
        check("tools/list returns the 12 tools", sorted(t["name"] for t in tools) == TOOLS, [t["name"] for t in tools])
        check("every tool has an object input schema", all(t["inputSchema"]["type"] == "object" for t in tools))
        check("unknown method is a JSON-RPC error", client.request("nope/nothing").get("error", {}).get("code") == -32601)
        check("unknown tool is a JSON-RPC error", "error" in client.request("tools/call", {"name": "nope", "arguments": {}}))

        print("KiCad")
        ok, info = client.tool("kicad_info")
        check("kicad_info reaches kicad-cli and KiCad's Python", ok and info.get("pcbnew_version"), info)
        if not ok:
            return
        print("        KiCad %s, Python at %s" % (info["kicad_version"], info["kicad_python"]))

        print("Libraries")
        ok, found = client.tool("search_library", kind="symbol", query="555 timer")
        check("symbol search finds the NE555", ok and "Timer:NE555P" in [r["id"] for r in found["results"]], found)
        ok, symbol = client.tool("library_item", kind="symbol", id="Timer:NE555P")
        pins = {p["number"]: p["name"] for p in symbol["pins"]} if ok else {}
        check("symbol details list the 8 pins by number and name", pins.get("1") == "GND" and pins.get("8") == "VCC" and len(pins) == 8, symbol)
        ok, symbol = client.tool("library_item", kind="symbol", id="Transistor_BJT:BC547")
        check("an inherited symbol resolves its base symbol's pins", ok and [p["name"] for p in symbol["pins"]] == ["C", "B", "E"], symbol)
        ok, found = client.tool("search_library", kind="footprint", query="DIP-8 W7.62mm")
        check("footprint search finds the DIP-8", ok and "Package_DIP:DIP-8_W7.62mm" in [r["id"] for r in found["results"]], found)
        ok, footprint = client.tool("library_item", kind="footprint", id="Package_DIP:DIP-8_W7.62mm")
        check("footprint details list 8 through-hole pads", ok and footprint["pad_count"] == 8 and footprint["mount"] == "through_hole", footprint)
        ok, out = client.tool("library_item", kind="symbol", id="Device:NoSuchPart")
        check("an unknown symbol is a tool error that points to search_library", not ok and "search_library" in str(out), out)

        print("Schematic")
        spec = load("blinky.json")
        ok, sch = client.tool("create_schematic", project="blinky", **spec)
        check("create_schematic writes a schematic KiCad's netlist agrees with", ok and "matches" in sch.get("verified", "") and sch["nets"] == 7, sch)
        check("no pins are left unconnected", ok and "unconnected_pins" not in sch, sch)
        ok, erc = client.tool("run_erc", project="blinky")
        check("ERC is clean", ok and erc["clean"] and erc["errors"] == 0, erc)
        broken = json.loads(json.dumps(spec))
        broken["components"] = [c for c in broken["components"] if not c["ref"].startswith("#FLG")]
        broken["nets"] = {net: [p for p in pins if not p.startswith("#FLG")] for net, pins in broken["nets"].items()}
        ok, _ = client.tool("create_schematic", project="unpowered", **broken)
        ok2, erc = client.tool("run_erc", project="unpowered")
        check("ERC catches a real fault (power pins not driven without PWR_FLAG)",
              ok and ok2 and erc["errors"] >= 1 and any(v["type"] == "power_pin_not_driven" for v in erc["violations"]), erc)
        ok, out = client.tool("create_schematic", project="bad", components=[{"ref": "R1", "symbol": "Device:R"}], nets={"A": ["R1.3"]})
        check("a wrong pin number is rejected and lists the real pins", not ok and "Its pins are: 1, 2" in str(out), out)
        ok, out = client.tool("create_schematic", project="bad", components=[{"ref": "R1", "symbol": "Device:R"}], nets={"A": ["R1.1"], "B": ["R1.1"]})
        check("a pin in two nets is rejected", not ok and "two nets" in str(out), out)
        check("rejected schematics leave no project behind", not os.path.exists(os.path.join(workdir, "bad")))

        print("Board")
        ok, sync = client.tool("update_pcb_from_schematic", project="blinky")
        check("update_pcb_from_schematic adds the 8 footprints", ok and len(sync["added"]) == 8 and sync["nets"] == 7, sync)
        ok, board = client.tool("inspect_board", project="blinky")
        check("before routing, connections are unrouted", ok and board["unrouted_connections"] > 0 and len(board["footprints"]) == 8, board)
        ok, edit = client.tool("edit_board", project="blinky", **load("blinky_board.json"))
        check("edit_board places, routes, pours and labels the board",
              ok and edit["track_segments_added"] == 28 and edit["zones_added"] == 1 and edit.get("zones_refilled") is True, edit)
        ok, board = client.tool("inspect_board", project="blinky")
        u1 = next((f for f in board["footprints"] if f["ref"] == "U1"), {}) if ok else {}
        pin8 = next((p for p in u1.get("pads", []) if p["number"] == "8"), {})
        check("inspect: outline is 40 x 33 mm and U1 pad 8 is on VCC where it was placed",
              ok and board["outline"]["size"] == [40.0, 33.0] and pin8.get("net") == "/VCC" and pin8.get("at") == [123.62, 108.0], (board.get("outline"), pin8))
        check("inspect: everything is routed and the ground zone is filled",
              ok and board["unrouted_connections"] == 0 and board["zones"] == [{"net": "/GND", "layers": ["B.Cu"], "filled": True}], board.get("zones"))
        ok, drc = client.tool("run_drc", project="blinky")
        check("DRC is clean: no violations, nothing unconnected, board matches schematic", ok and drc["clean"], drc)
        ok, _ = client.tool("edit_board", project="blinky", clear_nets=["LED_A"])
        ok2, drc = client.tool("run_drc", project="blinky")
        check("DRC catches a real fault (a removed track shows up as unconnected)", ok and ok2 and drc["unconnected"] == 1 and not drc["clean"], drc)
        ok, _ = client.tool("edit_board", project="blinky", tracks=[t for t in load("blinky_board.json")["tracks"] if t["net"] == "LED_A"])
        ok2, drc = client.tool("run_drc", project="blinky")
        check("re-adding the track makes DRC clean again", ok and ok2 and drc["clean"], drc)
        ok, sync = client.tool("update_pcb_from_schematic", project="blinky")
        check("re-running the update changes nothing", ok and len(sync["unchanged"]) == 8 and not sync["added"] and not sync["removed"], sync)
        ok, out = client.tool("edit_board", project="blinky", tracks=[{"net": "NOPE", "points": [[1, 1], [2, 2]]}])
        check("an unknown net is a tool error that lists the real nets", not ok and "/VCC" in str(out), out)

        print("Changing the design")
        changed = json.loads(json.dumps(spec))
        changed["components"] = [c for c in changed["components"] if c["ref"] not in ("D1", "R3")]
        next(c for c in changed["components"] if c["ref"] == "C2")["footprint"] = "Capacitor_THT:C_Disc_D3.0mm_W1.6mm_P2.50mm"
        changed["components"].append({"ref": "H1", "symbol": "Mechanical:MountingHole", "footprint": "MountingHole:MountingHole_3.2mm_M3"})
        changed["nets"] = {net: [p for p in pins if not p.startswith(("D1.", "R3."))] for net, pins in changed["nets"].items() if net not in ("LED_A", "OUT")}
        changed["no_connect"] = ["U1.3"]
        client.tool("create_schematic", project="variant", **spec)
        client.tool("update_pcb_from_schematic", project="variant")
        client.tool("edit_board", project="variant", place=[{"ref": "C2", "x": 116, "y": 121, "rotation": 90}])
        ok, sch = client.tool("create_schematic", project="variant", **changed)
        ok2, erc = client.tool("run_erc", project="variant")
        check("the changed schematic is verified and ERC-clean", ok and ok2 and erc["clean"], (sch, erc))
        ok, sync = client.tool("update_pcb_from_schematic", project="variant")
        check("the update adds H1, swaps C2's footprint and removes D1 and R3",
              ok and sync["added"] == ["H1"] and sync["replaced"] == ["C2"] and sync["removed"] == ["D1", "R3"], sync)
        ok, board = client.tool("inspect_board", project="variant", include_pads=False)
        c2 = next((f for f in board["footprints"] if f["ref"] == "C2"), {}) if ok else {}
        check("the swapped footprint keeps its position and rotation",
              c2.get("at") == [116.0, 121.0] and c2.get("rotation") == 90.0 and c2.get("footprint", "").endswith("P2.50mm"), c2)

        print("run_script")
        ok, out = client.tool("run_script", project="blinky", save=False,
                              code="print('hi')\nresult = [len(list(board.GetFootprints())), find_net('GND').GetNetname()]")
        check("script output and result come back", ok and out["stdout"] == "hi\n" and out["result"] == [8, "/GND"], out)
        ok, out = client.tool("run_script", project="blinky", code="board.Delete(board.FindFootprintByReference('R1'))\nraise ValueError('boom')")
        check("a failing script is a tool error with the traceback", not ok and "ValueError: boom" in out["error"], out)
        ok, board = client.tool("inspect_board", project="blinky", include_pads=False)
        check("a failing script does not save its changes", ok and len(board["footprints"]) == 8, board)

        print("Render and export")
        ok, out = client.tool("render", project="blinky", view="board_top")
        check("board render is a PNG", ok and os.path.getsize(out["image"]) > 10000 and open(out["image"], "rb").read(4) == b"\x89PNG", out)
        ok, out = client.tool("render", project="blinky", view="schematic")
        check("schematic render produces a PDF (and a PNG where a converter exists)", ok and os.path.getsize(out["pdf"]) > 5000, out)
        ok, out = client.tool("export", project="blinky", outputs=["gerbers", "position", "bom", "netlist", "schematic_pdf", "step"])
        files = out["files"] if ok else {}
        names = zipfile.ZipFile(files["gerbers"]["zip"]).namelist() if ok else []
        check("gerber zip holds copper, mask, silkscreen, outline and drill files",
              ok and all(any(n.endswith(ext) for n in names) for ext in (".gtl", ".gbl", ".gts", ".gto", ".gm1", ".drl")), names)
        bom = open(files["bom"], encoding="utf-8").read().splitlines() if ok else []
        check("BOM lists the 8 parts", len(bom) == 9 and bom[0].startswith('"Refs"'), bom)
        check("STEP, netlist, position and PDF files exist",
              ok and all(os.path.getsize(files[k]) > 100 for k in ("step", "netlist", "position", "schematic_pdf")), files)
    finally:
        code = client.close()
        check("server exits cleanly when stdin closes", code == 0, code)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    print("\n%s" % ("FAILED: %d check(s)" % len(failures) if failures else "All checks passed."))
    sys.exit(1 if failures else 0)
