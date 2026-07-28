# Supervise runtime tasks without failure propagation

An active FSM Runtime is an awaitable component scheduled inside a larger service and does not own the event loop, process signals, or daemon lifecycle. Every watch, reconciliation, lease, retry, and invocation loop is supervised so an ordinary `Exception` is reported and contained instead of escaping the runtime's `run()` coroutine or cancelling sibling tasks. Only external cancellation performs normal shutdown and is propagated after leases, watches, and child tasks are cleaned up.

Schema and processor finalization happen before activation and may fail fast because no runtime has started. The survival contract applies to recoverable Python exceptions; process termination, `MemoryError`, event-loop failure, and other `BaseException` conditions cannot be made recoverable guarantees.

Error hooks are isolated as user code as well: failure inside an error hook falls back to logging and cannot terminate supervision.
