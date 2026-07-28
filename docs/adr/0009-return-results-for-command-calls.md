# Return results for command calls

Submitting a Command Model creates a correlated Command Call whose initiator waits for a typed Command Result. The runtime records completion while releasing the State Key's Command Slot, allowing callers to distinguish an applied command from rejection, ignored work, and timeout. This makes the etcd-backed command mechanism request-response rather than fire-and-forget.

A command function returns its declared result type or `Retry`. `Applied[State]` commits and reports its contained State Value, `Rejected[Error]` carries a typed Pydantic error model, `Ignored` completes without a transition, and `Delete` removes the addressed object. `Retry` leaves the call pending. If no command function accepts the current State Discriminator and Command Model, the runtime completes the protocol operation with a typed dispatch exception rather than inventing a member of the user result type.

## Consequences

Each call needs a unique correlation identifier. Command Results require bounded persistence and cleanup so a disconnected caller neither loses an immediately completed result nor leaves storage occupied indefinitely.
