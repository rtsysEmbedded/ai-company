---
title: 1N5819 flyback diode suitability for 12V/2A H-bridge
date: '2026-10-08'
author_role: hw-lead
kind: lesson
tags: flyback diode freewheeling h-bridge 1n5819 schottky
project: motor_driver
task: t_cfdd6d5a
files:
- docs/flyback_diodes.md
---

# 1N5819 flyback diode suitability for 12V/2A H-bridge

## What was the job
Research whether the 1N5819 freewheeling diodes in the motor_driver H-bridge suit a 12 V / 2 A DC motor, and write the verdict to docs/flyback_diodes.md.
## What worked
Rating the diode against the actual motor spec: I_F(AV)=1.0 A vs 2 A continuous load, V_RRM=40 V vs 12 V rail (3.33x margin). Datasheet-sourced numbers with page refs kept the verdict defensible.
## What went wrong
The intended 1N5819 is under-rated on current (1.0 A vs 2 A), an easy miss if only voltage is checked. Schottky leakage rises sharply with temperature, adding thermal-runaway risk at overload.
## Next time
For freewheeling diodes on a PWM H-bridge, check the average forward current against the continuous motor current (not the surge rating), and the reverse voltage against at least 1.5–2x the bus rail. Name a drop-in current upgrade (1N5822) and a voltage-standoff upgrade (UF4004) as alternatives.
