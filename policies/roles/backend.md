# Role: backend
- Validate all external input at the boundary; use parameterised queries; no secrets in code (config via env / secret store).
- Privacy by design (GDPR Art. 25): store only the personal data the feature needs, pseudonymise where possible, define retention/deletion, support export and erasure of a user's data.
- Encrypt personal data in transit (TLS) and at rest; never log personal data, tokens or full request bodies.
- New dependency or external API: check licence, maintenance and whether it sends data outside the EU; mention it in the handoff.
- Device-facing APIs: authenticate every device, rate-limit, and version the protocol agreed with the hardware group.
