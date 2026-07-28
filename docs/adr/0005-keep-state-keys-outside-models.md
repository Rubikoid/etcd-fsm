---
status: superseded by ADR-0016
---

# Keep state keys outside state models

Every stored State Value is addressed by an explicit State Key supplied separately to the runtime. Reactions receive the typed key through their context, while State Models contain only state-specific data. This avoids requiring every model in an inheritance hierarchy to carry and preserve an identifier solely for persistence.
