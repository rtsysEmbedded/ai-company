---
title: 'STL up-scaling: split solids, scale by outer diameter, verify from disk'
date: '2026-10-06'
author_role: manager
kind: lesson
tags: 3d-printing,stl,cad,freecad
---

# STL up-scaling: split solids, scale by outer diameter, verify from disk

Scaling a downloaded binary STL up for printing (verified workflow):

1. Read binary STL directly: 80-byte header, uint32 tri count at offset 80, then 50 bytes/tri (12 floats + uint16). Validate len == 84 + n*50 to catch ASCII STLs (those must be converted by a slicer first).
2. Do NOT scale by bounding box unless the user says so - the bbox is usually set by a non-functional overhang. Instead split the mesh into connected solids (union-find over welded vertices, round coords to 3dp, since STL stores per-triangle vertices with no shared indices) and scale so the largest ROUND component's outer diameter hits the target. Roundness test: min(w,d)/max(w,d) close to 1.
3. Volume scales with the CUBE, so a 3.2x linear scale = 33x the plastic. Always report mass (solid volume x density: PLA 1.24, PETG 1.27 g/cm3) and check max part footprint against common bed sizes (220/250/300 mm).
4. Scale about the assembly's XY centre and reset Z min to 0. For split parts, additionally centre each part on its own XY centre so it can be sliced alone.
5. Verify by RE-READING the written file from disk and re-measuring size/volume, and check sum(part volumes) == assembly volume. Also count non-manifold edges (every edge must appear exactly twice) - a scaled download is often already watertight; report it rather than assuming.
6. Hardware caveat to always state: holes and pivot bores scale too, so screw sizes no longer match - call it out.

Reusable script: scale_stl.py (--target-od / --target-bbox / --scale, splits solids, writes report.txt).
