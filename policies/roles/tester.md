# Role: tester
- Use synthetic or anonymised test data only; never real personal data or production dumps.
- Cover edge cases and error paths, not just the happy path. Add a regression test for every fixed bug.
- Firmware: prefer host-based unit tests and simulators; mark tests that need real hardware (HIL) and do not run them without confirmation.
- Docs: for features handling personal data, document what is stored, why, and for how long. Never put secrets or real data in docs or examples.
