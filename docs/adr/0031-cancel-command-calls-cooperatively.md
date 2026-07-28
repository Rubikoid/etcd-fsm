# Cancel command calls cooperatively

Cancelling a durable Command Call atomically compares its call identifier and command revision, removes the still-pending `/rpc` entry, and records a retained `CommandCancelled` protocol result. Every command commit compares the same revision, so a handler that finishes after cancellation cannot mutate the State Value. A local running coroutine receives cooperative cancellation through the watch path.

Cancellation cannot reverse an external side effect that user code already performed. If normal completion commits first, a concurrent cancellation observes and returns that completed result rather than replacing it.
