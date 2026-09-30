# Threat model template (STRIDE)

Copy into the project folder in the kb and fill in. Keep it short; update it when the design changes.

## 1. Scope
- System / feature:
- Hardware parts (board, MCU, radios):
- Software parts (firmware, backend, frontend, cloud services):
- Personal data involved (yes/no, which):

## 2. Data flow
List components, data stores and the flows between them. Mark trust boundaries
(device <-> network, network <-> backend, backend <-> third parties, user <-> UI).

## 3. Threats per element (STRIDE)
| Element / flow | S spoofing | T tampering | R repudiation | I information disclosure | D denial of service | E elevation of privilege |
|---|---|---|---|---|---|---|
| | | | | | | |

## 4. Mitigations
| Threat | Mitigation | Status (planned/done) | Owner role |
|---|---|---|---|

## 5. Residual risk and privacy
- Accepted risks and why:
- GDPR checklist done? (knowledge/policies-full/gdpr-checklist.md)
- CRA device checklist done? (knowledge/policies-full/cra-secure-device-checklist.md)
