"""Generates a KiCad schematic from a declarative description (components + nets).

Every connected pin gets a short wire stub ending in a net label, so connectivity never depends
on drawing wires between symbols. The result is verified by the caller, which exports the
netlist with kicad-cli and compares it against `expected` using check_netlist().
"""
import copy
import datetime
import math
import re
import uuid
import xml.etree.ElementTree as ET

from kicad_lib import KiCadError, Libraries, Sym, child, children, dump, has_flag, num

STUB = 2.54            # wire stub between a pin and its label
GRID = 2.54            # symbol origins snap to this
CHAR_WIDTH = 1.1       # rough label width per character at 1.27 mm text
GAP = 7.62             # space between auto-placed symbols
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]
# File format written for each KiCad major version; newer KiCad upgrades it on load.
FORMAT_VERSION = {8: "20231120"}
DEFAULT_FORMAT_VERSION = "20260306"
_COORD_HEADS = ("xy", "start", "end", "mid", "center", "at")


def L(head, *items):
    return [Sym(head)] + list(items)


def _uid(project, key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "kicad-mcp:%s:%s" % (project, key)))


def _rotate(x, y, rotation):
    """Rotate a library point (Y up) counter-clockwise by a multiple of 90 degrees."""
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[rotation]


def _to_sheet(origin, x, y, rotation):
    """Library point -> sheet position. Sheet Y points down, library Y points up."""
    rx, ry = _rotate(x, y, rotation)
    return origin[0] + rx, origin[1] - ry


def _graphic_points(node):
    points = []
    for item in node:
        if not isinstance(item, list) or not item:
            continue
        if item[0] in _COORD_HEADS and len(item) >= 3:
            try:
                points.append((float(item[1]), float(item[2])))
            except ValueError:
                pass
        elif item[0] not in ("property", "name", "number"):
            points.extend(_graphic_points(item))
    return points


def _font():
    return L("font", L("size", num(1.27), num(1.27)))


def _field(name, value, x, y, angle, hide, justify=None):
    effects = L("effects", _font())
    if justify:
        effects.append(copy.deepcopy(justify))
    if hide:
        effects.append(L("hide", Sym("yes")))
    return L("property", name, value, L("at", num(x), num(y), num(angle)), effects)


def _flag(symbol_node, name):
    flag = child(symbol_node, name)
    return Sym("no") if flag is not None and len(flag) > 1 and flag[1] == "no" else Sym("yes")


class _Unit:
    """One placed unit of a component."""

    def __init__(self, comp, unit):
        self.comp = comp
        self.unit = unit
        self.pins = [p for p in comp["pins"] if p["unit"] in (0, unit)]
        self.origin = None

    def out_angle(self, pin):
        return (pin["angle"] + 180 + self.comp["rotation"]) % 360

    def relative_bbox(self, net_of):
        """Bounding box relative to the origin, in sheet orientation, including stubs and labels."""
        rotation = self.comp["rotation"]
        points = []
        for node in children(self.comp["node"], "symbol"):
            suffix = re.search(r"_(\d+)_(\d+)$", node[1])
            if suffix and int(suffix.group(1)) in (0, self.unit) and int(suffix.group(2)) <= 1:
                points.extend(_graphic_points(node))
        xs, ys = [], []
        for x, y in points or [(0.0, 0.0)]:
            sx, sy = _to_sheet((0.0, 0.0), x, y, rotation)
            xs.append(sx)
            ys.append(sy)
        for pin in self.pins:
            net = net_of.get((self.comp["ref"], pin["number"]))
            if net is None:
                continue
            sx, sy = _to_sheet((0.0, 0.0), pin["x"], pin["y"], rotation)
            reach = STUB + len(net) * CHAR_WIDTH + 1.0
            angle = math.radians(self.out_angle(pin))
            xs.append(sx + reach * round(math.cos(angle)))
            ys.append(sy - reach * round(math.sin(angle)))
        return min(xs) - 2.54, min(ys) - 3.81, max(xs) + 2.54, max(ys) + 3.81


def _resolve_pins(comp, key):
    by_number = [p for p in comp["pins"] if p["number"] == key]
    if by_number:
        return by_number
    by_name = [p for p in comp["pins"] if p["name"] == key and key not in ("", "~")]
    if by_name:
        return by_name
    known = ", ".join("%s(%s)" % (p["number"], p["name"]) if p["name"] not in ("", "~") else p["number"] for p in comp["pins"])
    raise KiCadError("%s has no pin %r. Its pins are: %s" % (comp["ref"], key, known))


def _split_pin(text, comps):
    if not isinstance(text, str) or "." not in text:
        raise KiCadError("Pins must be written 'REF.PIN' (for example 'R1.2' or 'U1.VCC'), got %r" % (text,))
    ref, key = text.split(".", 1)
    if ref not in comps:
        raise KiCadError("%r refers to component %r, which is not in 'components'" % (text, ref))
    return comps[ref], key


def build_schematic(spec, libs, project, root_uuid, kicad_major):
    """Return (schematic_text, report). `report["expected"]` maps net -> sorted 'REF.PIN' list."""
    components = spec.get("components") or []
    if not components:
        raise KiCadError("'components' must list at least one component")
    comps = {}
    for entry in components:
        ref = entry.get("ref")
        if not ref or not entry.get("symbol"):
            raise KiCadError("Every component needs 'ref' and 'symbol', got %r" % (entry,))
        if ref in comps:
            raise KiCadError("Duplicate component reference %r" % ref)
        rotation = int(entry.get("rotation") or 0)
        if rotation not in (0, 90, 180, 270):
            raise KiCadError("%s: rotation must be 0, 90, 180 or 270" % ref)
        node = libs.symbol(entry["symbol"])
        comps[ref] = {
            "ref": ref,
            "symbol": entry["symbol"],
            "node": node,
            "pins": Libraries.pins(node),
            "rotation": rotation,
            "value": entry.get("value") or Libraries.property_value(node, "Value"),
            "footprint": entry.get("footprint") or Libraries.property_value(node, "Footprint"),
            "fields": entry.get("fields") or {},
            "at": entry.get("at"),
            "is_power": child(node, "power") is not None,
        }
        if entry.get("footprint"):
            libs.footprint_path(entry["footprint"])  # fail early on a wrong footprint id

    # --- nets -----------------------------------------------------------------
    net_of = {}

    def assign(comp, pin, net):
        key = (comp["ref"], pin["number"])
        if net_of.get(key, net) != net:
            raise KiCadError("%s.%s is in two nets: %r and %r" % (comp["ref"], pin["number"], net_of[key], net))
        net_of[key] = net

    for net, members in (spec.get("nets") or {}).items():
        if not net or re.search(r"\s", net):
            raise KiCadError("Net names must be non-empty and contain no spaces, got %r" % net)
        for member in members:
            comp, key = _split_pin(member, comps)
            for pin in _resolve_pins(comp, key):
                assign(comp, pin, net)

    no_connect = set()
    for member in spec.get("no_connect") or []:
        comp, key = _split_pin(member, comps)
        for pin in _resolve_pins(comp, key):
            if (comp["ref"], pin["number"]) in net_of:
                raise KiCadError("%s is both in a net and in 'no_connect'" % member)
            no_connect.add((comp["ref"], pin["number"]))

    units = []
    for comp in comps.values():
        comp["units"] = [_Unit(comp, u) for u in range(1, Libraries.unit_count(comp["node"]) + 1)]
        comp["units"] = [u for u in comp["units"] if u.pins or len(comp["units"]) == 1]
        units.extend(comp["units"])
        # Pins drawn on top of each other are electrically one point: they share a net.
        for unit in comp["units"]:
            stacks = {}
            for pin in unit.pins:
                stacks.setdefault((pin["x"], pin["y"]), []).append(pin)
            for stack in stacks.values():
                nets = {net_of[(comp["ref"], p["number"])] for p in stack if (comp["ref"], p["number"]) in net_of}
                if len(nets) > 1:
                    raise KiCadError("%s pins %s sit on the same point but are in different nets %s"
                                     % (comp["ref"], [p["number"] for p in stack], sorted(nets)))
                for pin in stack:
                    if nets:
                        assign(comp, pin, next(iter(nets)))

    # --- placement ------------------------------------------------------------
    boxes = {id(u): u.relative_bbox(net_of) for u in units}
    for comp in comps.values():
        if comp["at"] is not None:
            x, y = float(comp["at"][0]), float(comp["at"][1])
            for unit in comp["units"]:
                unit.origin = (round(x / 1.27) * 1.27, round(y / 1.27) * 1.27)
                y += boxes[id(unit)][3] - boxes[id(unit)][1] + GAP
    auto = [u for u in units if u.origin is None]
    fixed = [u for u in units if u.origin is not None]
    need_w = max([u.origin[0] + boxes[id(u)][2] + 15 for u in fixed] + [0])
    need_h = max([u.origin[1] + boxes[id(u)][3] + 45 for u in fixed] + [0])
    paper = None
    for name, width, height in PAPERS:
        if width < need_w or height < need_h:
            continue
        x, y, row_height, fits = 20.0, 20.0, 0.0, True
        placement = []
        for unit in auto:
            min_x, min_y, max_x, max_y = boxes[id(unit)]
            w, h = max_x - min_x, max_y - min_y
            if x + w > width - 20 and x > 20.0:
                x, y, row_height = 20.0, y + row_height + GAP, 0.0
            placement.append((math.ceil((x - min_x) / GRID) * GRID, math.ceil((y - min_y) / GRID) * GRID))
            x += w + GAP
            row_height = max(row_height, h)
            if y + h > height - 45 or w > width - 40:
                fits = False
                break
        if fits:
            paper = name
            for unit, origin in zip(auto, placement):
                unit.origin = origin
            break
    if paper is None:
        raise KiCadError("The schematic does not fit on an A0 sheet; split the design or give explicit 'at' positions")

    # --- emit -----------------------------------------------------------------
    lib_symbols = L("lib_symbols")
    embedded = set()
    body = []
    unconnected = []
    for comp in comps.values():
        if comp["symbol"] not in embedded:
            embedded.add(comp["symbol"])
            node = copy.deepcopy(comp["node"])
            node[1] = comp["symbol"]
            lib_symbols.append(node)
        lib_props = {p[1]: p for p in children(comp["node"], "property")}
        for unit in comp["units"]:
            ox, oy = unit.origin
            rotation = comp["rotation"]
            instance = L(
                "symbol",
                L("lib_id", comp["symbol"]),
                L("at", num(ox), num(oy), num(rotation)),
                L("unit", num(unit.unit)),
                L("exclude_from_sim", Sym("no")),
                L("in_bom", _flag(comp["node"], "in_bom")),
                L("on_board", _flag(comp["node"], "on_board")),
                L("dnp", Sym("no")),
                L("uuid", _uid(project, "%s:%d" % (comp["ref"], unit.unit))),
            )
            values = {"Reference": comp["ref"], "Value": comp["value"], "Footprint": comp["footprint"],
                      "Datasheet": Libraries.property_value(comp["node"], "Datasheet"),
                      "Description": Libraries.property_value(comp["node"], "Description")}
            values.update({str(k): str(v) for k, v in comp["fields"].items()})
            for name, value in values.items():
                prop = lib_props.get(name)
                visible = name in ("Reference", "Value")
                if prop is not None and visible:
                    at = child(prop, "at")
                    fx, fy = _to_sheet((ox, oy), float(at[1]), float(at[2]), rotation)
                    effects = child(prop, "effects") or []
                    hide = has_flag(prop, "hide") or has_flag(effects, "hide")
                    instance.append(_field(name, value, fx, fy, float(at[3]) if len(at) > 3 else 0, hide, child(effects, "justify")))
                else:
                    instance.append(_field(name, value, ox, oy, 0, True))
            done = set()
            for pin in unit.pins:
                if pin["number"] not in done:
                    done.add(pin["number"])
                    instance.append(L("pin", pin["number"], L("uuid", _uid(project, "%s:%d:pin:%s" % (comp["ref"], unit.unit, pin["number"])))))
            instance.append(L("instances", L("project", project, L("path", "/" + root_uuid, L("reference", comp["ref"]), L("unit", num(unit.unit))))))
            body.append(instance)

            seen_points = set()
            for pin in unit.pins:
                key = (comp["ref"], pin["number"])
                px, py = _to_sheet((ox, oy), pin["x"], pin["y"], rotation)
                point = (round(px, 4), round(py, 4))
                tag = "%s:%d:%s" % (comp["ref"], unit.unit, pin["number"])
                if key in net_of:
                    if point in seen_points:
                        continue
                    seen_points.add(point)
                    out = unit.out_angle(pin)
                    radians = math.radians(out)
                    ex, ey = px + STUB * round(math.cos(radians)), py - STUB * round(math.sin(radians))
                    body.append(L("wire", L("pts", L("xy", num(px), num(py)), L("xy", num(ex), num(ey))),
                                  L("stroke", L("width", num(0)), L("type", Sym("default"))), L("uuid", _uid(project, "wire:" + tag))))
                    justify = L("justify", Sym("left" if out in (0, 90) else "right"), Sym("bottom"))
                    body.append(L("label", net_of[key], L("at", num(ex), num(ey), num(out)),
                                  L("effects", _font(), justify), L("uuid", _uid(project, "label:" + tag))))
                elif key in no_connect:
                    if point not in seen_points:
                        seen_points.add(point)
                        body.append(L("no_connect", L("at", num(px), num(py)), L("uuid", _uid(project, "nc:" + tag))))
                elif not (pin["hidden"] and pin["type"] == "power_in"):
                    unconnected.append("%s.%s" % key + (" (%s)" % pin["name"] if pin["name"] not in ("", "~") else ""))

    title = L("title_block", L("title", str(spec.get("title") or project)), L("date", datetime.date.today().isoformat()))
    for key in ("rev", "company"):
        if spec.get(key):
            title.append(L(key, str(spec[key])))
    root = L(
        "kicad_sch",
        L("version", Sym(FORMAT_VERSION.get(kicad_major, DEFAULT_FORMAT_VERSION))),
        L("generator", "kicad_mcp"),
        L("generator_version", "1.0"),
        L("uuid", root_uuid),
        L("paper", paper),
        title,
        lib_symbols,
    )
    root.extend(body)
    root.append(L("sheet_instances", L("path", "/", L("page", "1"))))

    expected = {}
    for (ref, number), net in net_of.items():
        if not ref.startswith("#"):
            expected.setdefault(net, set()).add("%s.%s" % (ref, number))
    report = {
        "paper": paper,
        "components": len(comps),
        "nets": len(set(net_of.values())),
        "placed": {u.comp["ref"] + ("" if len(u.comp["units"]) == 1 else " unit %d" % u.unit): [round(u.origin[0], 2), round(u.origin[1], 2)] for u in units},
        "expected": {net: sorted(nodes) for net, nodes in sorted(expected.items())},
    }
    if unconnected:
        report["unconnected_pins"] = sorted(set(unconnected))
        report["warning"] = "These pins are in no net and not in 'no_connect'; ERC will report them."
    return dump(root) + "\n", report


def read_netlist(xml_path):
    """Parse a kicad-cli 'kicadxml' netlist into components and nets."""
    root = ET.parse(xml_path).getroot()
    components = []
    for comp in root.iter("comp"):
        sheetpath = comp.find("sheetpath")
        components.append({
            "ref": comp.get("ref"),
            "value": comp.findtext("value") or "",
            "footprint": comp.findtext("footprint") or "",
            "fields": {f.get("name"): f.text or "" for f in comp.iter("field") if f.get("name") != "Footprint"},
            "path": ((sheetpath.get("tstamps") if sheetpath is not None else "/") or "/").rstrip("/") + "/" + (comp.findtext("tstamps") or "").split(" ")[0],
        })
    nets = {}
    for net in root.iter("net"):
        nets[net.get("name")] = sorted("%s.%s" % (n.get("ref"), n.get("pin")) for n in net.findall("node"))
    return {"components": components, "nets": nets}


def check_netlist(netlist, expected):
    """Compare the netlist KiCad produced with the requested nets. Returns (problems, kicad_net_names)."""
    net_of_node = {node: name for name, nodes in netlist["nets"].items() for node in nodes}
    problems, names = [], {}
    for net, nodes in expected.items():
        wanted = set(nodes)
        found = {net_of_node.get(node) for node in wanted}
        if None in found:
            missing = sorted(n for n in wanted if n not in net_of_node)
            problems.append("net %s: KiCad sees no connection at %s" % (net, ", ".join(missing)))
        elif len(found) > 1:
            problems.append("net %s is split into separate nets %s" % (net, sorted(found)))
        else:
            name = found.pop()
            extra = set(netlist["nets"][name]) - wanted
            if extra:
                problems.append("net %s is also connected to %s" % (net, ", ".join(sorted(extra))))
            names[net] = name
    return problems, names
