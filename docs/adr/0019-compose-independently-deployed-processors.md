# Compose independently deployed FSM processors

An FSM Schema is a shared package containing the complete state and command model contract, while each independently deployed FSM Processor registers only the Reactions, commands, and initializers that its service executes. Processors watch the same schema namespace and pass work by committing State Values that another processor recognizes. Their typed signatures form a Composed Graph without requiring every service to import every other service's behavior.

Processor and reaction names are stable parts of claim identity: replicas of the same processor compete for one logical Reaction, while different processors retain independent reactions. Each processor validates its local fragment at startup; integration tests import processor manifests to validate cross-service invariants such as a single Transitioning Reaction per concrete state.

An FSM Runtime owns infrastructure for one or more local processors and may bind processors from multiple schemas to one etcd connection.
