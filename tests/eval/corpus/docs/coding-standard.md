# Embedded C Coding Standard

## Naming
Functions use `module_action()` in snake_case. Macros are UPPER_CASE. Global variables start with `g_`.

## Memory
No `malloc` after initialization. Every buffer size is a compile-time constant.

## Interrupts
Interrupt service routines must be shorter than 5 microseconds; defer work to a task via a queue.
