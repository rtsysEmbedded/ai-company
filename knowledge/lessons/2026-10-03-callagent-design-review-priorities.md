---
title: CallAgent design review priorities
date: '2026-10-03'
author_role: manager
kind: lesson
tags: callagent architecture voice-ai safety telephony
files:
- DESIGN.md
- PLAN.md
---

# CallAgent design review priorities

The CallAgent design is strongest when browser conversation is treated as the first vertical slice and telephony is gated behind real audio/control experiments. Before implementation, make policy decisions atomic and enforce them outside the LLM, define authenticated/versioned event contracts with backpressure and reconnect behavior, and add explicit data retention/consent controls for voice, transcripts, identities, and embeddings.
