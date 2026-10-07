# L-shaped mounting bracket with two bolt holes in the base and two in the wall.
# Run it through the run_script tool with a document, e.g. document="bracket".
# `doc`, `Part` and `Vector` are provided by run_script. Units are millimetres.

width = 80        # along Y
depth = 80        # base length along X
height = 100      # wall height along Z
thickness = 15
hole_diameter = 8

base = Part.makeBox(depth, width, thickness)
wall = Part.makeBox(thickness, width, height)
bracket = base.fuse(wall).removeSplitter()

r = hole_diameter / 2
for y in (width * 0.25, width * 0.75):
    # Vertical holes through the base, clear of the wall.
    bracket = bracket.cut(Part.makeCylinder(r, thickness, Vector((depth + thickness) / 2, y, 0)))
    # Horizontal holes through the wall, above the base.
    bracket = bracket.cut(Part.makeCylinder(r, thickness, Vector(0, y, (height + thickness) / 2), Vector(1, 0, 0)))

# Boolean operations return a compound wrapping the one solid; unwrap it.
bracket = bracket.Solids[0]
obj = doc.addObject("Part::Feature", "Bracket")
obj.Shape = bracket
print("Bracket volume: %.1f mm^3" % bracket.Volume)
