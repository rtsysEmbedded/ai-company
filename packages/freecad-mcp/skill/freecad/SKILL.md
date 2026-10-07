---
name: freecad
description: Design, modify, measure and export 3D CAD parts with FreeCAD through the freecad MCP tools. Use when the user asks to model or design a 3D part (bracket, mount, enclosure, adapter, spacer, flange, jig), wants something for 3D printing or machining, or mentions FreeCAD, CAD, STEP, STL or .FCStd files.
---

# FreeCAD modelling

You build parts with the `freecad` MCP server's tools: `freecad_info`, `create_primitive`,
`boolean`, `inspect_document`, `export`, `run_script`. If those tools are not available, say so
and ask the user to run `install.sh` from the freecad-mcp folder and start a new session. Do not
fall back to writing scripts for the user to run by hand unless they ask.

FreeCAD runs headless: you cannot see the part. Numbers are your only evidence, so measuring
the result is part of the job, not an extra.

## Workflow

1. **Pin down the dimensions.** Units are millimetres. If a dimension that changes the part's
   function is missing (hole size, overall envelope, wall thickness), ask. For everything else
   choose a sensible value and state it as an assumption in your answer.
2. **Build.** One `run_script` call containing the whole part is the default: it is a single
   FreeCAD launch and the script is a reproducible record. Use `create_primitive` + `boolean`
   only for parts made of a few plain shapes.
3. **Verify** with the returned object summary (or `inspect_document`). See the checklist below.
4. **Export** with `export` when the user needs a file for another program: `.step` for CAD and
   machining, `.stl` for 3D printing.
5. **Report** the file paths, the final size and volume, and every assumption you made. Say that
   you checked it numerically and that they should open the `.FCStd` in FreeCAD to look at it.

A document name like `bracket` means `bracket.FCStd` in the workdir (`freecad_info` shows it).

## Writing scripts for run_script

`run_script` provides `FreeCAD`, `App`, `Part`, `Vector`, `WORKDIR` and, when you pass
`document`, `doc`. The document is saved automatically if the script finishes without raising;
do not call save yourself. Every call is a fresh FreeCAD process, so only saved files persist.

Skeleton that is safe to re-run:

```python
# Parameters first, so a change request is a one-line edit.
length, width, height = 40, 30, 10

for o in list(doc.Objects):          # rebuild from scratch; otherwise a re-run adds "Part001"
    doc.removeObject(o.Name)

part = Part.makeBox(length, width, height)
part = part.cut(Part.makeCylinder(3, height, Vector(length / 2, width / 2, 0)))
part = part.Solids[0]                # booleans return a Compound wrapping the solid

doc.addObject("Part::Feature", "Part").Shape = part
result = {"volume": part.Volume}     # assigning `result` returns a value; print() also comes back
```

Recipes that work:

```python
# Box from the origin corner; cylinder standing on a point along +Z, or along any axis
Part.makeBox(length, width, height, Vector(x, y, z))
Part.makeCylinder(radius, height, Vector(x, y, z))
Part.makeCylinder(radius, height, Vector(x, y, z), Vector(1, 0, 0))   # axis along +X

# Any 2D outline extruded: close the polygon by repeating the first point
outline = [Vector(0, 0, 0), Vector(40, 0, 0), Vector(40, 10, 0), Vector(10, 10, 0),
           Vector(10, 30, 0), Vector(0, 30, 0), Vector(0, 0, 0)]
solid = Part.Face(Part.makePolygon(outline)).extrude(Vector(0, 0, 20))

# Union and subtraction; removeSplitter() merges the leftover seam faces after a fuse
body = a.fuse([b, c]).removeSplitter()
body = body.cut(tool)

# Fillet / chamfer: select edges by geometry, never by index (indices change between runs)
vertical = [e for e in body.Edges if abs(e.tangentAt(e.FirstParameter).z) > 0.99]
body = body.makeFillet(3, vertical)
top = [e for e in body.Edges if abs(e.BoundBox.ZMin - height) < 1e-6]
body = body.makeChamfer(1, top)

# Circular pattern of holes
import math
for i in range(count):
    a = 2 * math.pi * i / count
    body = body.cut(Part.makeCylinder(r, thickness, Vector(R * math.cos(a), R * math.sin(a), 0)))

# Change an existing parametric object (made by create_primitive)
doc.getObject("Plate").Length = 50
```

Mistakes to avoid:

- **Order of operations with holes.** Cut bores and holes *after* fusing everything that
  surrounds them. Fusing a solid flange or cap onto a tube that was already bored seals the bore.
- **Hole tools must pass fully through.** Make the cutting cylinder at least as long as the
  material, and start it at or before the surface.
- **No GUI.** `FreeCADGui`, `ViewObject`, colours, screenshots and "active document" calls do
  not work. Use the `doc` you are given.
- **Do not use** `doc.save(path)` or `doc.close()`; they fail in this FreeCAD. Saving is
  automatic, and exports go through the `export` tool.
- **Fillets can fail** when the radius is too large for the edge's neighbours. If `makeFillet`
  raises, reduce the radius or fillet fewer edges, and tell the user what you changed.

## Verification checklist

Check every one before saying the part is done:

- `valid` is `true` and `shape_type` is `Solid`. A `Compound` means the pieces did not merge
  into one body (or you forgot `.Solids[0]`); more than one solid usually means parts that do
  not touch.
- `size` (bounding box) equals the intended overall dimensions.
- `volume_mm3` matches a rough hand calculation: solids added, holes subtracted. A volume equal
  to the un-drilled shape means a hole missed the part.
- Every through-hole is actually open. Test points with
  `part.isInside(Vector(x, y, z), 1e-6, True)`: it must be `False` on the hole axis at both ends
  and in the middle, and `True` somewhere in solid material.
- There are no leftover objects from earlier attempts in the document.

If a check fails, fix the script and run it again. Never describe a feature (a hole, a fillet,
a material) that you did not verify in the returned numbers.
