# Isolate invalid state keys

When stored data cannot be decoded by its FSM Schema, the runtime quarantines that State Key, reports a structured decode error, and continues processing other keys. It does not invoke Reactions or commands for the quarantined key and never mutates or deletes the invalid value automatically. This confines data corruption without hiding it or turning one bad record into a system-wide outage.

## Consequences

Direct operations on a quarantined key fail explicitly. Error reporting includes the key, raw value, and Pydantic validation details, and runtime health exposes the number of quarantined keys. Deployments may run a separate strict validation scan when any invalid value should block rollout.
