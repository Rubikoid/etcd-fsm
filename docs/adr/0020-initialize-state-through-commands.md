# Initialize state through typed commands

New State Values are created only by initializer functions invoked through durable Command Calls. An initializer accepts a concrete Command Model without a current State Value, and `Applied[State]` members in its resolved result annotation define the allowed initial State Models. Commit atomically verifies that the target State Key is still absent, writes the initial value, frees the command slot, and publishes the typed result.

There is no public `create(any_state)` escape hatch. The Command Model and returned State Model must derive the same composite key, and initialization uses the same retry, correlation, rejection, and idempotency semantics as other command processing.
