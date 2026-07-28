# Derive the transition graph from function signatures

The resolved signatures of transition-producing functions are the sole source of graph edges. A Transitioning Reaction or command function derives source states from its state parameter and match mode, while an initializer has no source state. Reaction annotations expose State Models directly; command annotations expose them through `Applied[State]`. `Delete` defines a deletion edge, and non-transition outcomes such as `Retry`, `Rejected`, `Ignored`, and `None` are removed. No separate `allow` declarations duplicate that information. Each FSM Processor resolves its local annotations when finalized and rejects unsupported or ambiguous signatures before its runtime starts. The complete graph is the union of signatures from all processors registered against the shared FSM Schema.

## Consequences

Changing a transition-producing function's type signature changes the graph. Runtime validation still rejects a returned State Value whose discriminator is absent from the resolved target union.
