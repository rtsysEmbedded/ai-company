# GDPR / DSGVO checklist for features and changes

Use for any change that collects, stores, processes or transmits personal data
(anything that identifies a person directly or indirectly: name, email, IP address,
device ID/serial linked to a user, location, voice/images, usage logs).

This is an engineering checklist, not legal advice. Unclear cases go to the data protection officer / legal.

## 1. Purpose and legal basis (Art. 5, 6)
- [ ] The purpose is written down and specific.
- [ ] A legal basis exists (contract, consent, legal obligation, legitimate interest).
- [ ] Data is not reused for a different purpose without a new basis.
- [ ] Special categories (health, biometrics, etc., Art. 9) are avoided or explicitly approved.

## 2. Data minimisation and privacy by design/default (Art. 5, 25)
- [ ] Only fields that the purpose needs are collected.
- [ ] Pseudonymise or anonymise where the purpose allows (e.g. hashed IDs, aggregated telemetry).
- [ ] Privacy-friendly defaults: optional data and tracking are off until the user opts in.

## 3. Storage and retention (Art. 5(1)(e), 17)
- [ ] Retention period defined; automatic deletion implemented.
- [ ] Erasure of one user's data is possible (including backups policy and derived data).
- [ ] Data export for one user is possible (access / portability, Art. 15, 20).

## 4. Security (Art. 32)
- [ ] Encryption in transit (TLS 1.2+) and at rest.
- [ ] Access restricted to roles that need it; access is logged.
- [ ] No personal data in application logs, crash reports, analytics, test data or LLM prompts.
- [ ] Secrets in a secret store, rotated, never in source code.

## 5. Processors and transfers (Art. 28, 44 ff.)
- [ ] Every external service that receives personal data is listed; it has a data processing agreement (AVV).
- [ ] Data stays in the EU/EEA, or the transfer mechanism (adequacy decision, SCCs) is documented.
- [ ] Third-party scripts, fonts, CDNs, SDKs in web/mobile/firmware checked for hidden data transfers.

## 6. Web and apps (TTDSG / ePrivacy)
- [ ] Non-essential cookies/local storage/tracking only after consent; reject is as easy as accept.
- [ ] Privacy notice updated when new data is collected.

## 7. Records and assessments
- [ ] Record of processing activities (Art. 30) updated for new processing.
- [ ] DPIA (Art. 35) considered for high-risk processing (large-scale monitoring, location tracking, biometrics, new tech).
- [ ] Data breach path known: detect, report to the authority within 72 h (Art. 33).
