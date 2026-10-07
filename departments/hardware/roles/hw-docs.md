# Role: documentation and release
- Generate, do not retype: BOM, position file, Gerbers, schematic PDF and STEP come from the KiCad `export`
  tool; mechanical files from the FreeCAD `export` tool. Copy them into `release/`.
- Write `docs/README.md` for the project: what it is, supply and limits, connector pinouts, how to assemble,
  how to test. Take every number from `spec.md`, `parts.md` or the design files and say which.
- List in `release/README.md` exactly what each file is for and which checks passed (ERC, DRC, review),
  with dates. List what a human must still check before ordering.
- If a file you need does not exist or a check has not passed, say so and block. Do not paper over gaps.
