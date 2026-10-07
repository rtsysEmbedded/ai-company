# Pipe connector: a hollow tube with a bolted flange at each end.
# Run it through the run_script tool with a document, e.g. document="connector".
# `doc`, `Part` and `Vector` are provided by run_script. Units are millimetres.
import math

bore_diameter = 24
tube_diameter = 30
flange_diameter = 60
flange_thickness = 5
total_length = 60
bolt_count = 4
bolt_diameter = 5
bolt_circle_diameter = 45

tube = Part.makeCylinder(tube_diameter / 2, total_length)
bottom_flange = Part.makeCylinder(flange_diameter / 2, flange_thickness)
top_flange = Part.makeCylinder(flange_diameter / 2, flange_thickness, Vector(0, 0, total_length - flange_thickness))
connector = tube.fuse([bottom_flange, top_flange]).removeSplitter()

connector = connector.cut(Part.makeCylinder(bore_diameter / 2, total_length))
for i in range(bolt_count):
    angle = 2 * math.pi * i / bolt_count
    x = bolt_circle_diameter / 2 * math.cos(angle)
    y = bolt_circle_diameter / 2 * math.sin(angle)
    # One full-length cylinder drills the matching hole in both flanges.
    connector = connector.cut(Part.makeCylinder(bolt_diameter / 2, total_length, Vector(x, y, 0)))

# Boolean operations return a compound wrapping the one solid; unwrap it.
connector = connector.Solids[0]
obj = doc.addObject("Part::Feature", "Connector")
obj.Shape = connector
print("Connector volume: %.1f mm^3" % connector.Volume)
