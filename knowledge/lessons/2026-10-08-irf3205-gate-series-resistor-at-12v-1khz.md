---
title: IRF3205 gate series resistor at 12V/1kHz
date: '2026-10-08'
author_role: hw-lead
kind: research
tags: mosfet gate-resistor irf3205 motor-driver
project: motor_driver
task: t_5bdb8591
files:
- docs/gate_resistor.md
---

# IRF3205 gate series resistor at 12V/1kHz

## Question
Usual gate series resistor value for an IRF3205 switched at ~1 kHz from a small discrete transistor stage at 12 V, and why.

## Findings
47 Ω to 150 Ω (typical 100 Ω). At 100 Ω peak gate current is 120 mA (protects 2N3904/BC847 driver transistors), the gate LC loop is overdamped (critical damping ~7.8 Ω), and the ~1.5 µs transition is <0.3% of the 1 ms PWM period (~24 mW switching loss). Lower bound ~47 Ω set by 200 mA peak driver limit; upper bound ~150-220 Ω set by keeping transition <5 µs.

## Sources
Infineon IRF3205PbF datasheet; TI SLUA618; TI SLLA385; Nexperia AN90059; Toshiba AKX00068; Diodes BC847BFA; Diotec 2N3904. See docs/gate_resistor.md section 4 for URLs.

## Open points
none
