# Let command calls outlive their callers

A Command Call remains pending and continues processing when its initiator times out or disconnects. Local timeout stops only the wait and returns a stable Command Handle; the occupied Command Slot continues to provide backpressure until the call completes or is explicitly cancelled. This avoids treating loss of an RPC waiter as proof that distributed work and its external side effects did not start.

## Consequences

Callers can resume waiting or request cancellation using the call identifier. Completed Command Results remain retrievable for a bounded retention period. Explicit cancellation must be coordinated with in-flight processing and cannot promise reversal of an external side effect that already occurred.
