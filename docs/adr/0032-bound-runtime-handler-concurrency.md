# Bound runtime handler concurrency

The FSM Runtime schedules user functions through a bounded global concurrency limit, with optional stricter limits per Reaction, command function, or initializer. Local capacity is acquired before a Processing Claim or Transition Claim so a service does not hold distributed ownership while merely queued. This protects the host service and external dependencies during large reconciliation scans.

Queued work is deduplicated by schema, State Key, State Revision, and function identity; stale revisions are discarded. Watches continue to be consumed independently of slow handlers, and retries re-enter the same scheduler rather than spawning unbounded tasks.
