# RC servo mount, modelled after the Carson X-Crawlee Pro "Servomount" line drawing (500405602).
# Two pieces: a post that the servo ear screws onto, and a clamp plate that goes over the ear.
# The drawing has no dimensions: every value below is an assumption sized for a standard servo.
# Run through the run_script tool with a document, e.g. document="servomount". Units: mm.

# --- Shared -----------------------------------------------------------------
ear_hole_spacing = 10.0     # distance between the two screw holes of one servo ear
clearance_hole = 3.2        # M3 screw passes through
pilot_hole = 2.5            # M3 screw threads into the plastic

# --- Clamp plate (lies flat, thickness along Z) ------------------------------
plate_length = 20.0
plate_width = 9.0
plate_thickness = 4.0
plate_chamfer = 0.8         # on the top outer edge
plate_offset_x = 25.0       # where the plate sits next to the post in the document

# --- Post (flat face in the XZ plane, thickness along Y) ---------------------
post_height = 40.0
post_width = 9.0
post_thickness = 4.0
slot_width = 3.0
slot_bottom, slot_top = 10.0, 21.0      # overall extent of the slot along Z
standoff_length = 12.0                  # how far the standoffs stick out behind the post
side_boss_diameter = 8.0
side_boss_x, side_boss_z = -14.0, 15.5  # centre of the side standoff
arm_width = 8.0

for o in list(doc.Objects):
    doc.removeObject(o.Name)

Y = Vector(0, 1, 0)


def slab(x0, z0, x1, z1, width, depth, y0=0.0):
    """Rounded-end bar between two points in the XZ plane, `depth` thick along +Y."""
    r = width / 2.0
    axis = Vector(x1 - x0, 0, z1 - z0)
    side = Vector(-axis.z, 0, axis.x)
    side.normalize()
    side = side * r
    a, b = Vector(x0, y0, z0), Vector(x1, y0, z1)
    rect = Part.Face(Part.makePolygon([a + side, b + side, b - side, a - side, a + side])).extrude(Y * depth)
    ends = [Part.makeCylinder(r, depth, p, Y) for p in (a, b)]
    return rect.fuse(ends).removeSplitter()


def drill(shape, diameter, x, z, depth):
    """Hole along Y through `depth` of material, overshooting both faces."""
    return shape.cut(Part.makeCylinder(diameter / 2.0, depth + 2, Vector(x, -1, z), Y))


# --- Build the post ----------------------------------------------------------
r_post = post_width / 2.0
total_depth = post_thickness + standoff_length
servo_hole_top_z = post_height - r_post
servo_hole_low_z = servo_hole_top_z - ear_hole_spacing

upright = slab(0, r_post, 0, post_height - r_post, post_width, post_thickness)
arm = slab(side_boss_x, side_boss_z, 0, side_boss_z, arm_width, post_thickness)
foot_boss = Part.makeCylinder(r_post, total_depth, Vector(0, 0, r_post), Y)
side_boss = Part.makeCylinder(side_boss_diameter / 2.0, total_depth, Vector(side_boss_x, 0, side_boss_z), Y)
post = upright.fuse([arm, foot_boss, side_boss]).removeSplitter()

# All holes are cut after everything around them is fused.
post = drill(post, clearance_hole, 0, r_post, total_depth)                 # foot standoff
post = drill(post, clearance_hole, side_boss_x, side_boss_z, total_depth)  # side standoff
post = drill(post, pilot_hole, 0, servo_hole_top_z, post_thickness)        # servo ear, upper
post = drill(post, pilot_hole, 0, servo_hole_low_z, post_thickness)        # servo ear, lower
r_slot = slot_width / 2.0
post = post.cut(slab(0, slot_bottom + r_slot, 0, slot_top - r_slot, slot_width, post_thickness + 2, y0=-1))
post = post.Solids[0]

# --- Build the clamp plate ---------------------------------------------------
r_plate = plate_width / 2.0
half = plate_length / 2.0 - r_plate
plate = Part.makeBox(2 * half, plate_width, plate_thickness, Vector(-half, -r_plate, 0))
plate = plate.fuse([Part.makeCylinder(r_plate, plate_thickness, Vector(s * half, 0, 0)) for s in (-1, 1)]).removeSplitter()
volume_before_chamfer = plate.Volume
top_edges = [e for e in plate.Edges if abs(e.BoundBox.ZMin - plate_thickness) < 1e-6]
plate = plate.makeChamfer(plate_chamfer, top_edges)
chamfer_removed = volume_before_chamfer - plate.Volume
for s in (-1, 1):
    plate = plate.cut(Part.makeCylinder(clearance_hole / 2.0, plate_thickness + 2, Vector(s * ear_hole_spacing / 2.0, 0, -1)))
plate = plate.Solids[0]
plate.translate(Vector(plate_offset_x, 0, 0))

doc.addObject("Part::Feature", "Post").Shape = post
doc.addObject("Part::Feature", "ClampPlate").Shape = plate


# --- Self-check: is each hole open, is the material solid? -------------------
def solid_at(shape, x, y, z):
    return shape.isInside(Vector(x, y, z), 1e-6, True)


def hole_open(shape, x, z, depth):
    return not any(solid_at(shape, x, y, z) for y in (0.01, depth / 2.0, depth - 0.01))


px = plate_offset_x
result = {
    "post_foot_hole_open": hole_open(post, 0, r_post, total_depth),
    "post_side_hole_open": hole_open(post, side_boss_x, side_boss_z, total_depth),
    "post_servo_hole_top_open": hole_open(post, 0, servo_hole_top_z, post_thickness),
    "post_servo_hole_low_open": hole_open(post, 0, servo_hole_low_z, post_thickness),
    "post_slot_open": all(hole_open(post, 0, z, post_thickness) for z in (slot_bottom + 0.1, (slot_bottom + slot_top) / 2, slot_top - 0.1)),
    "post_solid_between_servo_holes": solid_at(post, 0, post_thickness / 2, (servo_hole_top_z + servo_hole_low_z) / 2),
    "post_solid_in_arm": solid_at(post, -8, post_thickness / 2, side_boss_z),
    "post_solid_in_foot_standoff_wall": solid_at(post, 3, total_depth - 1, r_post),
    "post_solid_in_side_standoff_wall": solid_at(post, side_boss_x - 3, total_depth - 1, side_boss_z),
    "plate_holes_open": all(not solid_at(plate, px + s * ear_hole_spacing / 2, 0, z) for s in (-1, 1) for z in (0.01, plate_thickness / 2, plate_thickness - 0.01)),
    "plate_solid_in_middle": solid_at(plate, px, 0, plate_thickness / 2),
    "plate_volume_before_chamfer_and_holes": round(volume_before_chamfer, 2),
    "plate_chamfer_removed": round(chamfer_removed, 2),
    "servo_hole_spacing": servo_hole_top_z - servo_hole_low_z,
}
