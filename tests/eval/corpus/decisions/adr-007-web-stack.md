# ADR-007: Web dashboard stack

## Decision
The device configuration dashboard is built with Vue 3, Vite and Tailwind CSS.
It talks to the device through a REST API served by the gateway (FastAPI).
Authentication uses short-lived JWT tokens stored in memory, never in localStorage.
