# Attach claims to one runtime session lease

Each FSM Runtime instance owns one etcd session lease and attaches all of its Processing Claims and Transition Claims to that lease. A single keepalive stream therefore represents the liveness of the whole runtime instance instead of maintaining a lease per State Key. Lease TTL and keepalive interval are configurable and validated as a safe pair.

On lease loss, the runtime cancels its owned user invocations, stops acquiring work, and enters its supervised reconnect and reconciliation loop. State commits compare the original State Revision and claim ownership, so a partitioned former owner cannot commit after losing its session. External effects remain at-least-once and require idempotency because lease-loss detection cannot retract work already performed.
