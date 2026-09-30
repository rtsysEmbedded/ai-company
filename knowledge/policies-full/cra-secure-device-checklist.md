# Secure device checklist (EU Cyber Resilience Act oriented)

For hardware/firmware and connected products with digital elements. Based on the
CRA essential requirements and common practice (ETSI EN 303 645, IEC 62443-4-2).
Engineering checklist, not legal advice.

## Secure by design and by default
- [ ] Threat model exists for the device and its interfaces (radio, USB, debug, network, cloud API).
- [ ] No universal default passwords; unique per-device credentials or forced setup.
- [ ] Unused interfaces, services and debug ports (JTAG/SWD/UART console) disabled or locked in production.
- [ ] Minimal attack surface: only needed services and ports are enabled.

## Boot and update
- [ ] Secure boot / verified boot with a hardware root of trust where available.
- [ ] Firmware updates are signed, verified before install, and rollback-protected.
- [ ] Update failure leaves the device in a working state (A/B or recovery image).
- [ ] Security updates can be delivered for the whole declared support period.

## Keys and data
- [ ] Private keys and secrets in secure element / protected flash, never in source or images.
- [ ] Communication authenticated and encrypted (TLS/DTLS, modern ciphers); certificates validated.
- [ ] Personal data on the device minimised, encrypted, and erasable (factory reset wipes it).

## Code quality
- [ ] Input from radio/network/USB is treated as untrusted and bounds-checked.
- [ ] Compiler hardening and static analysis enabled where the toolchain supports it.
- [ ] Memory-safety review for C/C++ (bounds, integer overflow, use-after-free, stack size).

## Vulnerability handling
- [ ] SBOM (software bill of materials) produced for each firmware release.
- [ ] Dependencies (RTOS, stacks, libraries) tracked for CVEs.
- [ ] A way for researchers to report vulnerabilities exists; actively exploited vulnerabilities must be reported (ENISA) within the CRA deadlines.
