# Store only the current state

Each tracked entity stores only its latest State Value. State Change Notifications wake consumers so they can reconcile against that value, but they do not form a durable event history and missed intermediate states are not replayed. This keeps persistence and recovery simple because the library does not require transition history.

## Consequences

A consumer that reconnects may observe only the latest State Value. Reactions cannot depend on receiving every intermediate change.
