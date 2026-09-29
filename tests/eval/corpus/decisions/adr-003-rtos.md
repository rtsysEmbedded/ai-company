# ADR-003: Use FreeRTOS instead of Zephyr

## Status
Accepted, 2026-03-12.

## Context
The motor controller needs deterministic task switching under 10 microseconds and must fit in 256 KB flash.

## Decision
We use FreeRTOS 11 with static allocation only. Zephyr was rejected because its build system
and device tree added too much complexity for a two-person team.

## Consequences
No dynamic memory after startup: all queues and tasks use `xTaskCreateStatic` / `xQueueCreateStatic`.
