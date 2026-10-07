---
name: kicad
description: Design electronics with KiCad through the kicad MCP tools - schematics, PCB layout, ERC/DRC checks, renders, and Gerber/BOM/STEP export. Use when the user asks to design, draw, lay out, route, check or export a circuit, schematic, PCB or board, wants manufacturing files, or mentions KiCad, Gerbers, footprints, netlists, .kicad_sch or .kicad_pcb files.
---

# KiCad design

You work with the `kicad` MCP server's tools: `kicad_info`, `search_library`, `library_item`,
`create_schematic`, `run_erc`, `update_pcb_from_schematic`, `edit_board`, `inspect_board`,
`run_drc`, `render`, `export`, `run_script`. If those tools are not available, say so and ask
the user to run `install.sh` from the kicad-mcp folder and start a new session.

A design is finished only when the tools say so: ERC clean, DRC clean with zero unconnected
items, and you have looked at the renders. Report what the checks returned, not what you intended.

## Workflow

1. **Agree the circuit.** If something that changes the function is missing (supply voltage,
   load, connector type, through-hole or SMD), ask. Choose the rest yourself and list your
   choices as assumptions in the final answer.
2. **Find every part.** `search_library` for the symbol and for the footprint. Never invent an
   id. Then `library_item` on each symbol: wire by the pin numbers it reports, not from memory.
   Check the footprint's pad numbers match the symbol's pin numbers (a 3-pin transistor is the
   classic trap: the same symbol exists in several pin orders).
3. **`create_schematic`** with all components and nets. It replaces the whole schematic, so
   always send the complete design. It refuses to save unless KiCad's netlist matches your nets.
4. **`run_erc`** and fix what it reports (see below). Zero errors before moving on.
5. **`update_pcb_from_schematic`** to put the footprints and nets on the board. Run it again
   after every schematic change.
6. **Lay out** with `edit_board`: outline, placement, tracks, zones. Use `inspect_board` to get
   real pad coordinates; never guess them.
7. **`run_drc`** until `clean` is true. Then `render` the board (top and bottom) and the
   schematic, and look at the images.
8. **`export`** what the user needs: `gerbers` for a board house, `bom`, `position`, `step`,
   `schematic_pdf`.
9. **Report** the project folder, the files, the ERC/DRC results, and every assumption.

A project `blinky` lives in `<workdir>/blinky/`; `kicad_info` shows the workdir and existing projects.

## Schematic rules

- Nets are lists of pins written `REF.PIN`: `"VCC": ["U1.8", "R1.1"]`. `PIN` is the pin number,
  or a pin name such as `U1.GND`, which selects every pin with that name.
- **Every pin must be in a net or in `no_connect`.** `create_schematic` returns
  `unconnected_pins` when you missed some.
- **Power needs a `PWR_FLAG`.** When power comes in through a connector, ERC reports
  `power_pin_not_driven`. Add `{"ref": "#FLG01", "symbol": "power:PWR_FLAG"}` and put
  `#FLG01.1` in the supply net; do the same with `#FLG02` for ground.
- Give every component that belongs on the board a `footprint`. Components without one are
  skipped by `update_pcb_from_schematic`.
- Mounting holes are components too: symbol `Mechanical:MountingHole`, footprint e.g.
  `MountingHole:MountingHole_3.2mm_M3`.
- Net names show up in KiCad with a leading slash (`/VCC`). The board tools accept either form.
- The schematic is drawn with a net label on every pin instead of wires. That is intentional;
  do not try to add wires.
- Leave `at` out for automatic placement.

Common ERC findings:

| Type | Meaning | Fix |
| --- | --- | --- |
| `power_pin_not_driven` | A power input has no source KiCad recognises | Add a `power:PWR_FLAG` to that net |
| `pin_not_connected` | A pin is in no net | Add it to a net or to `no_connect` |
| `isolated_pin_label` | A net has only one pin | Usually a typo in a net or pin; or the net really is unused |
| `pin_to_pin` | Two outputs, or output to power, tied together | Check the wiring against the datasheet |

## Board rules

Coordinates are millimetres, origin top-left, **Y points down**. Rotation is counter-clockwise.

- **Order:** outline, then place, then `inspect_board`, then route. A footprint's `x`/`y` is its
  origin, which is often pad 1 rather than the centre, so read the pad positions back after placing.
- **Tracks** are polylines on one layer. Every track must start and end exactly on a pad centre
  or on a point where another track of the same net ends. Use 45 degree bends.
- **Widths:** 0.25 mm for signals, 0.5 mm or more for power. Default clearance is 0.2 mm, so
  keep track centres at least `width/2 + 0.2 + pad_radius` away from other nets' pads.
- **Ground:** add `"zones": [{"net": "GND", "layer": "B.Cu"}]`. On a through-hole board that
  connects every ground pad with no tracks at all. Zones are filled for you.
- **Layers:** through-hole pads exist on both copper layers, so a track on `B.Cu` can connect
  two of them directly. SMD pads exist on one layer only; to reach the other side add a via
  and continue on the other layer. A track on `B.Cu` cuts through the ground zone, so keep
  those short.
- **Crossings:** two nets cannot cross on the same layer. Route one of them on the other layer,
  or around.
- **Redoing a layout:** send `"clear": ["tracks", "zones", "texts"]` with the new one, or
  `"clear_nets": ["OUT"]` to rip up single nets. Without it, new tracks are added on top of old ones.
- **After removing components** from the schematic, their old tracks stay on the board and DRC
  reports `track_dangling`; clear them.

Common DRC findings:

| Type | Fix |
| --- | --- |
| unconnected items | A net is not fully routed, or a track end misses the pad centre. Compare with `inspect_board`. |
| `clearance`, `shorting_items` | Copper of two nets too close or touching. Move the track or use the other layer. |
| `copper_edge_clearance` | Copper within 0.5 mm of the board edge. Move it in or enlarge the outline. |
| `courtyards_overlap` | Two parts too close. Use `body_min`/`body_max` from `inspect_board` to space them. |
| `silk_overlap`, `silk_edge_clearance` | Reference text collides with something. Move the part, or move the text with `run_script`. |
| `track_dangling` | Leftover or half-finished track. Remove it. |

## run_script (board only)

For what `edit_board` cannot do. Provided names: `pcbnew`, `board`, `mm()`, `to_mm()`,
`xy(x, y)`, `find_net(name)`. The board is saved if the script finishes.

```python
fp = board.FindFootprintByReference("C2")
fp.Reference().SetPosition(xy(118.5, 124))   # move the reference text
fp.Value().SetVisible(False)                 # hide the value text

for track in [t for t in board.GetTracks() if t.GetNetname() == "/OUT"]:
    board.Delete(track)                      # Delete, never Remove

result = [fp.GetReference() for fp in board.GetFootprints()]
```

- **Use `board.Delete(item)`, never `board.Remove(item)`.** `Remove` corrupts KiCad's Python
  bindings a moment later and the script fails with unrelated-looking errors.
- **Do not fill zones in a script** (`ZONE_FILLER` crashes outside the GUI). They are refilled
  automatically after the script.
- There is no scripting access to the schematic; change it with `create_schematic`.

## Limits to be honest about

- There is no autorouter: you place every track by coordinates. This works for small and
  medium boards. For a dense board, say so, route the critical nets, and tell the user the
  rest needs interactive routing in KiCad.
- Only KiCad's stock libraries are searched. A part that is not there needs a generic symbol
  (`Device:*`, `Connector_Generic:*`) and a footprint chosen from its datasheet dimensions;
  tell the user which substitutions you made.
- ERC and DRC check rules, not whether the circuit does what the user wants. Component values,
  ratings and pinouts taken from memory are assumptions: say so and point to the datasheet.
