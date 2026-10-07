"""Executes one job inside FreeCAD's own Python (launched by freecad_mcp.py via freecadcmd).

The job is a JSON file named by $FREECAD_MCP_JOB; the result is written to job["result_path"].
Results go through a file because FreeCAD prints its own banner and warnings to stdout.
"""
import contextlib
import io
import json
import os
import re
import sys
import traceback

import FreeCAD
import Part

MAX_OUTPUT = 20000

PRIMITIVES = {
    "box": ("Part::Box", [("length", "Length"), ("width", "Width"), ("height", "Height")]),
    "cylinder": ("Part::Cylinder", [("radius", "Radius"), ("height", "Height")]),
    "sphere": ("Part::Sphere", [("radius", "Radius")]),
    "cone": ("Part::Cone", [("radius1", "Radius1"), ("radius2", "Radius2"), ("height", "Height")]),
    "torus": ("Part::Torus", [("radius1", "Radius1"), ("radius2", "Radius2")]),
}


class JobError(Exception):
    pass


def _round(value):
    return round(float(value), 4)


def _vec(v):
    return [_round(v.x), _round(v.y), _round(v.z)]


def describe(obj):
    info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    if "Invalid" in getattr(obj, "State", []):
        info["recompute_failed"] = True
    used_by = [o.Name for o in obj.InList]
    if used_by:
        info["used_by"] = used_by
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return info
    box = shape.BoundBox
    info.update(
        shape_type=shape.ShapeType,
        valid=shape.isValid(),
        volume_mm3=_round(shape.Volume),
        area_mm2=_round(shape.Area),
        bbox_min=[_round(box.XMin), _round(box.YMin), _round(box.ZMin)],
        bbox_max=[_round(box.XMax), _round(box.YMax), _round(box.ZMax)],
        size=[_round(box.XLength), _round(box.YLength), _round(box.ZLength)],
    )
    try:
        info["center_of_gravity"] = _vec(shape.CenterOfGravity)
    except Exception:
        pass
    return info


def summarize(doc, path):
    return {"document": path, "objects": [describe(o) for o in doc.Objects]}


def open_document(path, create):
    if os.path.exists(path):
        return FreeCAD.openDocument(path), False
    if not create:
        raise JobError("Document not found: %s" % path)
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"\W", "_", stem) or "Document"
    if name[0].isdigit():
        name = "_" + name
    doc = FreeCAD.newDocument(name)
    doc.Label = stem
    return doc, True


def save_document(doc, path, is_new):
    doc.recompute()
    if is_new:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.saveAs(path)
    else:
        doc.save()


def find_object(doc, name):
    obj = doc.getObject(name)
    if obj is None:
        matches = doc.getObjectsByLabel(name)
        obj = matches[0] if matches else None
    if obj is None:
        raise JobError(
            "No object named %r. Available: %s" % (name, ", ".join(o.Name for o in doc.Objects) or "(none)")
        )
    return obj


def placement(args):
    position = args.get("position") or [0, 0, 0]
    axis = args.get("rotation_axis") or [0, 0, 1]
    angle = args.get("rotation_angle") or 0
    return FreeCAD.Placement(FreeCAD.Vector(*position), FreeCAD.Rotation(FreeCAD.Vector(*axis), angle))


def op_info(args, workdir):
    version = FreeCAD.Version()
    return {
        "freecad_version": ".".join(version[:3]),
        "freecad_build": version[3] if len(version) > 3 else "",
        "python_version": sys.version.split()[0],
        "workdir": workdir,
    }


def op_create_primitive(args, workdir):
    kind = args["shape"]
    if kind not in PRIMITIVES:
        raise JobError("Unknown shape %r. Use one of: %s" % (kind, ", ".join(sorted(PRIMITIVES))))
    type_id, dims = PRIMITIVES[kind]
    missing = [key for key, _ in dims if args.get(key) is None]
    if missing:
        raise JobError("A %s needs: %s (missing: %s)" % (kind, ", ".join(k for k, _ in dims), ", ".join(missing)))
    doc, is_new = open_document(args["document"], create=True)
    obj = doc.addObject(type_id, args.get("name") or kind.capitalize())
    for key, prop in dims:
        setattr(obj, prop, args[key])
    obj.Placement = placement(args)
    save_document(doc, args["document"], is_new)
    return {"document": args["document"], "created": describe(obj)}


def op_boolean(args, workdir):
    doc, _ = open_document(args["document"], create=False)
    operation = args["operation"]
    base = find_object(doc, args["base"])
    tools = [find_object(doc, name) for name in args["tools"]]
    if not tools:
        raise JobError("'tools' must name at least one object")
    name = args.get("name") or operation.capitalize()
    if operation == "fuse":
        obj = doc.addObject("Part::MultiFuse", name)
        obj.Shapes = [base] + tools
    elif operation == "common":
        obj = doc.addObject("Part::MultiCommon", name)
        obj.Shapes = [base] + tools
    elif operation == "cut":
        tool = tools[0]
        if len(tools) > 1:
            tool = doc.addObject("Part::MultiFuse", name + "_tools")
            tool.Shapes = tools
        obj = doc.addObject("Part::Cut", name)
        obj.Base = base
        obj.Tool = tool
    else:
        raise JobError("Unknown operation %r. Use fuse, cut or common." % operation)
    # Hide the inputs so only the result shows when the file is opened in the FreeCAD GUI.
    for operand in [base] + tools:
        try:
            operand.Visibility = False
        except Exception:
            pass
    save_document(doc, args["document"], False)
    return {"document": args["document"], "created": describe(obj)}


def op_inspect(args, workdir):
    doc, _ = open_document(args["document"], create=False)
    return summarize(doc, args["document"])


def op_export(args, workdir):
    doc, _ = open_document(args["document"], create=False)
    doc.recompute()
    names = args.get("objects")
    if names:
        objs = [find_object(doc, n) for n in names]
    else:
        # Objects nothing else depends on, i.e. the final results rather than their inputs.
        objs = [o for o in doc.Objects if not o.InList]
    objs = [o for o in objs if getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    if not objs:
        raise JobError("Nothing to export: no objects with geometry were selected")
    shape = objs[0].Shape if len(objs) == 1 else Part.makeCompound([o.Shape for o in objs])
    output = args["output"]
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ext = os.path.splitext(output)[1].lower()
    if ext in (".step", ".stp"):
        shape.exportStep(output)
    elif ext in (".iges", ".igs"):
        shape.exportIges(output)
    elif ext in (".brep", ".brp"):
        shape.exportBrep(output)
    elif ext == ".stl":
        import MeshPart

        mesh = MeshPart.meshFromShape(
            Shape=shape, LinearDeflection=args.get("linear_deflection") or 0.1, AngularDeflection=0.5
        )
        mesh.write(output)
    else:
        raise JobError("Unsupported format %r. Use .step, .stl, .iges or .brep" % ext)
    return {"output": output, "bytes": os.path.getsize(output), "exported": [o.Name for o in objs]}


def op_run_script(args, workdir):
    path = args.get("document")
    doc, is_new = (None, False)
    if path:
        doc, is_new = open_document(path, create=True)
    scope = {
        "__name__": "__main__",
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "Part": Part,
        "Vector": FreeCAD.Vector,
        "doc": doc,
        "WORKDIR": workdir,
    }
    out = io.StringIO()
    result = {}
    error = None
    os.chdir(workdir)
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
    if doc is not None:
        if args.get("save", True):
            save_document(doc, path, is_new)
            result["saved"] = True
        else:
            doc.recompute()
            result["saved"] = False
        result.update(summarize(doc, path))
    return result


OPERATIONS = {
    "freecad_info": op_info,
    "create_primitive": op_create_primitive,
    "boolean": op_boolean,
    "inspect_document": op_inspect,
    "export": op_export,
    "run_script": op_run_script,
}


def main():
    with open(os.environ["FREECAD_MCP_JOB"], "r", encoding="utf-8") as handle:
        job = json.load(handle)
    workdir = job["workdir"]
    try:
        os.makedirs(workdir, exist_ok=True)
        payload = OPERATIONS[job["op"]](job.get("args") or {}, workdir)
        failed = "error" in payload
    except JobError as exc:
        payload, failed = {"error": str(exc)}, True
    except Exception:
        payload, failed = {"error": traceback.format_exc()}, True
    with open(job["result_path"], "w", encoding="utf-8") as handle:
        json.dump({"ok": not failed, "payload": payload}, handle)


main()
