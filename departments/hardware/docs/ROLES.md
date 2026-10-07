# Hardware department: roles and workflow

## How a project flows

```
you -> hw-lead: "design X"
         |  writes spec.md, decides the interfaces, creates tasks
         v
   hw-research ---> hw-schematic ---> hw-layout ---> hw-docs
                         |                |
                         |                +--> hw-mech (fits the real board)
                         +--> hw-firmware (pin map from the schematic)

   every design task ends in a review by hw-review, which approves it or sends it back
```

The lead skips roles a project does not need. A bracket needs only `hw-mech`; a breakout board
needs no firmware.

Tasks live on Hermes's kanban board. A task has one assignee, a body that states the goal and the
acceptance criteria, and parent tasks that must finish first. Hermes's dispatcher starts the
assigned profile when the task is ready. A worker sees only its task body and its parents'
hand-offs, which is why the lead must write every interface decision into the task body.

## Project folder

`~/Hardware_Projects/<project>/` (change the base folder in the web interface):

| Path | Content | Owner |
|---|---|---|
| `spec.md` | Requirements, architecture, interface decisions | hw-lead |
| `parts.md` | Chosen parts, datasheet links, facts with page numbers | hw-research |
| `electronics/<project>/` | KiCad project, renders, exports | hw-schematic, hw-layout |
| `mechanical/` | FreeCAD documents, STEP and STL | hw-mech |
| `firmware/` | Source code | hw-firmware |
| `docs/` | BOM, pinout, assembly notes | hw-docs |
| `release/` | The files for a manufacturer | hw-docs |

## What each role is told

The exact text is in `departments/hardware/policies/department.md` (shared) and
`departments/hardware/roles/<role>.md`. In short:

**All roles.** Work only inside the project folder. A task is done when a tool says so: quote
ERC/DRC counts, measurements, test output. Never state a pin number, rating or dimension from
memory as fact. List every assumption. Never order, flash, power or send anything. Flag mains
voltage, large batteries and safety functions to the user.

**hw-lead.** Write `spec.md` first and decide the interfaces (rails, connectors, pin map, board
outline and mounting holes) so two specialists never invent the same thing differently. Create
tasks with precise bodies. Do not accept "done" without numbers. Tell the user what was verified,
what was assumed, and what a human must still check.

**hw-research.** One choice and one alternative per part, in production and available. Read the
datasheet itself. Record pinout, limits, reference circuit and layout notes with page numbers.
Say when a part only just meets a requirement. Do not guess prices or stock.

**hw-schematic.** Work from `spec.md` and `parts.md`. Check every symbol's pins and that the
footprint's pads match. Decoupling per datasheet, protection on external pins, no floating
inputs. Done means a verified schematic, clean ERC, and a look at the render.

**hw-layout.** Outline and holes exactly as specified. Place first, route second, from real pad
positions. Ground pour, power tracks sized for their current. Done means DRC clean, zero
unconnected, renders checked. If the board is too dense to route by hand, say which nets are left
and stop; never present a half-routed board as finished. Never change the schematic.

**hw-mech.** Take outline, holes and heights from the real board. State clearances as numbers.
Design for the manufacturing method. Done means a valid solid, measurements matching the
calculation, holes tested open, STEP and STL exported.

**hw-firmware.** Pin map from the schematic, in one header. Cite the datasheet for every
register and timing value. Embedded-C discipline. No flashing of real hardware. State what was
tested where.

**hw-review.** Do not trust the hand-off: re-run the checks. Review schematics against
datasheets, boards against the spec, mechanics against the real board, firmware against the
schematic. Approve with evidence or return with numbered fixes. Do not fix things yourself.

**hw-docs.** Generate files from the tools instead of retyping. Write the project README and a
release README listing each file and which checks passed. Block when something is missing.

## Changing the rules

Edit `roles/<role>.md` or `policies/department.md` in `departments/hardware/`, then `./dept apply hardware` (or "Save and apply"
in the web interface). The text goes into a marked block in each profile's `SOUL.md`; anything
you wrote outside that block is kept.

## Adding or removing a role

Roles are the entries of `"roles"` in `departments/hardware/department.json`. To add one, copy an existing entry under
a new name (lowercase letters, digits and `-`), add `roles/<name>.md`, and apply. To retire one,
untick "Enabled" in the web interface; its Hermes profile stays until you delete it with
`hermes profile delete <name>`.
