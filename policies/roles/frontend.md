# Role: frontend
- No third-party trackers, analytics, fonts or CDNs that transmit user IPs without prior consent (GDPR/TTDSG); prefer self-hosted assets.
- Non-essential cookies/storage only after opt-in consent; "reject" must be as easy as "accept".
- Never put personal data in URLs, query strings or client logs; do not store tokens in localStorage.
- Forms: collect only needed fields, link the privacy notice where personal data is entered.
- Escape output (XSS), use CSP-friendly code, and keep basic accessibility (labels, contrast, keyboard use).
