"""Executes one board job inside KiCad's own Python (launched by kicad_mcp.py).

The job is a JSON file named by $KICAD_MCP_JOB; the result is written to job["result_path"].
Results go through a file because KiCad prints warnings of its own to stdout and stderr.

Two rules learned the hard way with KiCad 10's Python bindings:
  * remove items with board.Delete(item); board.Remove(item) corrupts the bindings once the
    removed object is garbage collected;
  * never fill zones here (ZONE_FILLER crashes outside the GUI); the server refills them
    through `kicad-cli pcb drc --refill-zones --save-board`.
"""
import contextlib
import io
import json
import os
import sys
import traceback

import pcbnew

MAX_OUTPUT = 20000
MAX_TRACKS_LISTED = 400
MM = pcbnew.FromMM


class JobError(Exception):
    pass


def to_mm(value):
    return round(pcbnew.ToMM(value), 4)


def xy(x, y):
    return pcbnew.VECTOR2I(MM(float(x)), MM(float(y)))


def pos(point):
    return [to_mm(point.x), to_mm(point.y)]


def open_board(path, create):
    if os.path.exists(path):
        return pcbnew.LoadBoard(path), False
    if not create:
        raise JobError("There is no board yet at %s. Run update_pcb_from_schematic first." % path)
    return pcbnew.BOARD(), True


def save_board(board, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    pcbnew.SaveBoard(path, board)


def net_names(board):
    return sorted(str(name) for name in board.GetNetsByName().keys() if str(name))


def find_net(board, name):
    for candidate in (name, "/" + name):
        net = board.FindNet(candidate)
        if net is not None and net.GetNetCode() > 0:
            return net
    raise JobError("No net %r on the board. Nets: %s" % (name, ", ".join(net_names(board)) or "(none)"))


def find_footprint(board, ref):
    footprint = board.FindFootprintByReference(ref)
    if footprint is None:
        refs = sorted(fp.GetReference() for fp in board.GetFootprints())
        raise JobError("No footprint %r on the board. Footprints: %s" % (ref, ", ".join(refs) or "(none)"))
    return footprint


def layer_id(board, name):
    layer = board.GetLayerID(name)
    if layer < 0:
        raise JobError("Unknown layer %r. Use names such as F.Cu, B.Cu, F.SilkS, B.SilkS, Edge.Cuts." % name)
    return layer


def zone_layers(board, zone):
    return [board.GetLayerName(layer) for layer in zone.GetLayerSet().Seq()]


def outline_box(board):
    """Extent of the board edge, measured on the line centres (KiCad's own box adds the line width)."""
    edges = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    if not edges:
        return None
    x1 = y1 = x2 = y2 = None
    for edge in edges:
        box, half = edge.GetBoundingBox(), edge.GetWidth() // 2
        left, top = box.GetX() + half, box.GetY() + half
        right, bottom = box.GetX() + box.GetWidth() - half, box.GetY() + box.GetHeight() - half
        x1, y1 = (left if x1 is None else min(x1, left)), (top if y1 is None else min(y1, top))
        x2, y2 = (right if x2 is None else max(x2, right)), (bottom if y2 is None else max(y2, bottom))
    return {"min": [to_mm(x1), to_mm(y1)], "max": [to_mm(x2), to_mm(y2)], "size": [to_mm(x2 - x1), to_mm(y2 - y1)]}


def describe_footprint(footprint, include_pads):
    info = {
        "ref": footprint.GetReference(),
        "value": footprint.GetValue(),
        "footprint": footprint.GetFPIDAsString(),
        "at": pos(footprint.GetPosition()),
        "rotation": round(footprint.GetOrientationDegrees(), 2),
        "side": "bottom" if footprint.IsFlipped() else "top",
    }
    box = footprint.GetBoundingBox(False)
    info["body_min"] = [to_mm(box.GetX()), to_mm(box.GetY())]
    info["body_max"] = [to_mm(box.GetX() + box.GetWidth()), to_mm(box.GetY() + box.GetHeight())]
    if include_pads:
        info["pads"] = [
            {
                "number": pad.GetNumber(),
                "net": pad.GetNetname(),
                "at": pos(pad.GetPosition()),
                "type": "smd" if pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD else "through_hole",
            }
            for pad in footprint.Pads()
        ]
    return info


def summarize(board, path, include_pads=True):
    tracks, vias = [], []
    for item in board.GetTracks():
        if item.GetClass() == "PCB_VIA":
            vias.append({"net": item.GetNetname(), "at": pos(item.GetPosition()),
                         "size": to_mm(item.GetWidth(pcbnew.F_Cu)), "drill": to_mm(item.GetDrillValue())})
        else:
            tracks.append({"net": item.GetNetname(), "from": pos(item.GetStart()), "to": pos(item.GetEnd()),
                           "width": to_mm(item.GetWidth()), "layer": item.GetLayerName()})
    footprints = sorted(board.GetFootprints(), key=lambda fp: fp.GetReference())
    nets = {}
    for footprint in footprints:
        for pad in footprint.Pads():
            if pad.GetNetname():
                nets.setdefault(pad.GetNetname(), []).append("%s.%s" % (footprint.GetReference(), pad.GetNumber()))
    result = {
        "board": path,
        "copper_layers": board.GetCopperLayerCount(),
        "outline": outline_box(board),
        "footprints": [describe_footprint(fp, include_pads) for fp in footprints],
        "nets": {name: sorted(pads) for name, pads in sorted(nets.items())},
        "track_segments": len(tracks),
        "tracks": tracks[:MAX_TRACKS_LISTED],
        "vias": vias,
        "zones": [{"net": z.GetNetname(), "layers": zone_layers(board, z), "filled": bool(z.IsFilled())} for z in board.Zones()],
        "units": "mm, origin top-left, Y pointing down",
    }
    if len(tracks) > MAX_TRACKS_LISTED:
        result["tracks_note"] = "Only the first %d track segments are listed." % MAX_TRACKS_LISTED
    try:
        result["unrouted_connections"] = board.GetConnectivity().GetUnconnectedCount(True)
    except Exception:
        pass
    return result


def op_info(args):
    return {"kicad_version": pcbnew.Version(), "python_version": sys.version.split()[0]}


def op_inspect(args):
    board, _ = open_board(args["board"], create=False)
    return summarize(board, args["board"], args.get("include_pads", True))


def apply_symbol(footprint, comp):
    """Copy what the schematic knows about a component onto its footprint, as KiCad's own update does."""
    footprint.SetValue(comp["value"])
    footprint.SetPath(pcbnew.KIID_PATH(comp["path"]))
    for name, value in (comp.get("fields") or {}).items():
        existed = footprint.HasField(name)
        footprint.SetField(name, value)
        if not existed:
            footprint.GetField(name).SetVisible(False)


def op_sync(args):
    """Make the board's footprints and nets match the schematic netlist."""
    path = args["board"]
    board, is_new = open_board(path, create=True)
    existing = {fp.GetReference(): fp for fp in board.GetFootprints()}
    wanted = {c["ref"]: c for c in args["components"]}
    added, replaced, removed, kept, skipped = [], [], [], [], []

    # New footprints are parked in rows below whatever is already on the board.
    cursor_x, cursor_y, row_height = 30.0, 30.0, 0.0
    if not is_new and (existing or outline_box(board)):
        box = board.ComputeBoundingBox(False)
        cursor_y = to_mm(box.GetY() + box.GetHeight()) + 10.0

    for ref, comp in sorted(wanted.items()):
        if not comp["footprint"]:
            skipped.append(ref)
            continue
        lib, name = comp["footprint"].split(":", 1)
        old = existing.get(ref)
        if old is not None and old.GetFPIDAsString() == comp["footprint"]:
            apply_symbol(old, comp)
            kept.append(ref)
            continue
        footprint = pcbnew.FootprintLoad(args["footprint_dirs"][lib], name)
        if footprint is None:
            raise JobError("Could not load footprint %s for %s" % (comp["footprint"], ref))
        footprint.SetFPID(pcbnew.LIB_ID(lib, name))
        footprint.SetReference(ref)
        apply_symbol(footprint, comp)
        if old is not None:
            footprint.SetPosition(old.GetPosition())
            footprint.SetOrientationDegrees(old.GetOrientationDegrees())
            flipped = old.IsFlipped()
            board.Delete(old)
            board.Add(footprint)
            if flipped:
                footprint.Flip(footprint.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            replaced.append(ref)
        else:
            box = footprint.GetBoundingBox(False)
            width, height = to_mm(box.GetWidth()), to_mm(box.GetHeight())
            if cursor_x + width > 250.0 and cursor_x > 30.0:
                cursor_x, cursor_y, row_height = 30.0, cursor_y + row_height + 5.0, 0.0
            # Put the body's top-left corner at the cursor, whatever the footprint's own origin is.
            origin = footprint.GetPosition()
            footprint.SetPosition(pcbnew.VECTOR2I(MM(cursor_x) + origin.x - box.GetX(), MM(cursor_y) + origin.y - box.GetY()))
            cursor_x += width + 5.0
            row_height = max(row_height, height)
            board.Add(footprint)
            added.append(ref)

    if args.get("remove_extra", True):
        for ref, footprint in sorted(existing.items()):
            if ref not in wanted:
                board.Delete(footprint)
                removed.append(ref)

    pad_net = {}
    for name, nodes in args["nets"].items():
        net = board.FindNet(name)
        if net is None:
            net = pcbnew.NETINFO_ITEM(board, name)
            board.Add(net)
        for node in nodes:
            pad_net[tuple(node.split(".", 1))] = net
    missing_pads = []
    for footprint in board.GetFootprints():
        ref = footprint.GetReference()
        if ref not in wanted:
            continue
        numbers = set()
        for pad in footprint.Pads():
            numbers.add(pad.GetNumber())
            net = pad_net.get((ref, pad.GetNumber()))
            if net is not None:
                pad.SetNet(net)
            else:
                pad.SetNetCode(0)
        missing_pads.extend("%s.%s" % key for key in pad_net if key[0] == ref and key[1] not in numbers)

    save_board(board, path)
    result = {"board": path, "created": is_new, "added": added, "replaced": replaced, "removed": removed,
              "unchanged": kept, "nets": len(args["nets"])}
    if skipped:
        result["skipped_no_footprint"] = skipped
        result["warning"] = "These components have no footprint in the schematic, so they are not on the board."
    if missing_pads:
        result["pins_without_pad"] = sorted(missing_pads)
        result["pin_warning"] = "The schematic connects pins that the chosen footprint has no pad for. Check the footprint choice."
    if added:
        result["next"] = "New footprints are parked in a row. Place them with edit_board, then route."
    return result


def op_edit(args):
    path = args["board"]
    board, _ = open_board(path, create=False)
    done = {}

    clear = set(args.get("clear") or [])
    unknown = clear - {"tracks", "zones", "outline", "texts"}
    if unknown:
        raise JobError("'clear' accepts tracks, zones, outline, texts; got %s" % ", ".join(sorted(unknown)))
    clear_nets = None
    if args.get("clear_nets"):
        clear_nets = {find_net(board, n).GetNetname() for n in args["clear_nets"]}
    if "tracks" in clear or clear_nets:
        doomed = [t for t in board.GetTracks() if clear_nets is None or t.GetNetname() in clear_nets]
        for item in doomed:
            board.Delete(item)
        done["tracks_and_vias_removed"] = len(doomed)
    if "zones" in clear:
        doomed = list(board.Zones())
        for item in doomed:
            board.Delete(item)
        done["zones_removed"] = len(doomed)
    if "texts" in clear:
        doomed = [d for d in board.GetDrawings() if d.GetClass() == "PCB_TEXT"]
        for item in doomed:
            board.Delete(item)
        done["texts_removed"] = len(doomed)

    outline = args.get("outline")
    if outline or "outline" in clear:
        for item in [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]:
            board.Delete(item)
    if outline:
        shape = pcbnew.PCB_SHAPE(board)
        if outline.get("rect"):
            x1, y1, x2, y2 = outline["rect"]
            shape.SetShape(pcbnew.SHAPE_T_RECT)
            shape.SetStart(xy(min(x1, x2), min(y1, y2)))
            shape.SetEnd(xy(max(x1, x2), max(y1, y2)))
        elif outline.get("polygon") and len(outline["polygon"]) >= 3:
            shape.SetShape(pcbnew.SHAPE_T_POLY)
            shape.SetPolyPoints([xy(x, y) for x, y in outline["polygon"]])
        else:
            raise JobError("'outline' needs 'rect': [x1, y1, x2, y2] or 'polygon': [[x, y], ...] with at least 3 points")
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetWidth(MM(0.1))
        board.Add(shape)
        done["outline"] = outline_box(board)

    for item in args.get("place") or []:
        footprint = find_footprint(board, item["ref"])
        side = item.get("side")
        if side not in (None, "top", "bottom"):
            raise JobError("%s: side must be 'top' or 'bottom'" % item["ref"])
        if side is not None and (side == "bottom") != footprint.IsFlipped():
            footprint.Flip(footprint.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        if "rotation" in item:
            footprint.SetOrientationDegrees(float(item["rotation"]))
        if "x" in item or "y" in item:
            if "x" not in item or "y" not in item:
                raise JobError("%s: give both x and y" % item["ref"])
            footprint.SetPosition(xy(item["x"], item["y"]))
        done.setdefault("placed", []).append(item["ref"])

    segments = 0
    for item in args.get("tracks") or []:
        net = find_net(board, item["net"])
        points = item.get("points") or []
        if len(points) < 2:
            raise JobError("Track on %s needs at least two points" % item["net"])
        layer = layer_id(board, item.get("layer", "F.Cu"))
        if not pcbnew.IsCopperLayer(layer):
            raise JobError("Tracks must be on a copper layer, got %r" % item.get("layer"))
        for start, end in zip(points, points[1:]):
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(xy(*start))
            track.SetEnd(xy(*end))
            track.SetWidth(MM(float(item.get("width", 0.25))))
            track.SetLayer(layer)
            track.SetNet(net)
            board.Add(track)
            segments += 1
    if segments:
        done["track_segments_added"] = segments

    for item in args.get("vias") or []:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(xy(*item["at"]))
        via.SetWidth(pcbnew.F_Cu, MM(float(item.get("size", 0.8))))
        via.SetDrill(MM(float(item.get("drill", 0.4))))
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        via.SetNet(find_net(board, item["net"]))
        board.Add(via)
        done["vias_added"] = done.get("vias_added", 0) + 1

    for item in args.get("zones") or []:
        polygon = item.get("polygon")
        if not polygon:
            box = outline_box(board)
            if box is None:
                raise JobError("A zone without 'polygon' covers the board outline, but the board has no outline yet")
            (x1, y1), (x2, y2) = box["min"], box["max"]
            polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        zone = pcbnew.ZONE(board)
        layer = layer_id(board, item.get("layer", "B.Cu"))
        if not pcbnew.IsCopperLayer(layer):
            raise JobError("Zones must be on a copper layer, got %r" % item.get("layer"))
        zone.SetLayer(layer)
        zone.SetNet(find_net(board, item["net"]))
        if "clearance" in item:
            zone.SetLocalClearance(MM(float(item["clearance"])))
        chain = zone.Outline()
        chain.NewOutline()
        for x, y in polygon:
            chain.Append(MM(float(x)), MM(float(y)))
        board.Add(zone)
        done["zones_added"] = done.get("zones_added", 0) + 1

    for item in args.get("texts") or []:
        text = pcbnew.PCB_TEXT(board)
        text.SetText(str(item["text"]))
        text.SetPosition(xy(*item["at"]))
        layer_name = item.get("layer", "F.SilkS")
        text.SetLayer(layer_id(board, layer_name))
        size = float(item.get("size", 1.0))
        text.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        text.SetTextThickness(MM(size * 0.15))
        if layer_name.startswith("B."):
            text.SetMirrored(True)
        board.Add(text)
        done["texts_added"] = done.get("texts_added", 0) + 1

    if not done:
        raise JobError("Nothing to do: give at least one of outline, place, tracks, vias, zones, texts, clear, clear_nets")
    save_board(board, path)
    done["board"] = path
    done["has_zones"] = len(list(board.Zones())) > 0
    return done


def op_run_script(args):
    path = args.get("board")
    board = None
    if path:
        board, _ = open_board(path, create=True)
    scope = {"__name__": "__main__", "pcbnew": pcbnew, "board": board, "mm": MM, "to_mm": to_mm, "xy": xy,
             "find_net": (lambda name: find_net(board, name)), "WORKDIR": args["workdir"]}
    out = io.StringIO()
    result, error = {}, None
    os.chdir(args["workdir"])
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            exec(compile(args["code"], "<run_script>", "exec"), scope)
        except (Exception, SystemExit):
            error = traceback.format_exc()
    result["stdout"] = out.getvalue()[-MAX_OUTPUT:]
    if error:
        result["error"] = error
        result["saved"] = False
        return result
    if "result" in scope:
        try:
            json.dumps(scope["result"])
            result["result"] = scope["result"]
        except (TypeError, ValueError):
            result["result"] = repr(scope["result"])
    result["saved"] = False
    if board is not None and args.get("save", True):
        save_board(board, path)
        result["saved"] = True
        result["board"] = path
        result["has_zones"] = len(list(board.Zones())) > 0
    return result


OPERATIONS = {"info": op_info, "inspect": op_inspect, "sync": op_sync, "edit": op_edit, "run_script": op_run_script}


def main():
    with open(os.environ["KICAD_MCP_JOB"], "r", encoding="utf-8") as handle:
        job = json.load(handle)
    try:
        payload = OPERATIONS[job["op"]](job.get("args") or {})
        failed = "error" in payload
    except JobError as exc:
        payload, failed = {"error": str(exc)}, True
    except Exception:
        payload, failed = {"error": traceback.format_exc()}, True
    with open(job["result_path"], "w", encoding="utf-8") as handle:
        json.dump({"ok": not failed, "payload": payload}, handle)


main()
