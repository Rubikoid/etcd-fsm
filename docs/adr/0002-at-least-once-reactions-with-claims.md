# Execute named reactions at least once using claims

Each named Reaction on a State Revision is executed by one competing consumer at a time. Consumers acquire a Processing Claim with a lease before execution and retry after an abandoned claim expires. This prevents concurrent duplicate work across application replicas without treating a crashed consumer as permanent completion. When a Reaction completes without a transition, its owner keeps the claim alive until the State Revision changes or the owner disconnects.

## Consequences

Exactly-once execution is not guaranteed: a consumer can complete an external action and fail before recording the resulting transition. A successful Reaction does not create a durable completion receipt, so an unchanged State Revision is processed again after its claim owner disconnects and another consumer reconciles it. Reactions that perform external work must therefore use the stable idempotency key supplied for their State Revision and reaction name. A transition to a new State Revision ends processing of the old revision.
