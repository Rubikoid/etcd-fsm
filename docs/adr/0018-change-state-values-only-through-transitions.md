# Change state values only through transitions

An existing State Value can change or be removed only through an edge derived from a transition-producing function signature; there is no graph-bypassing update or delete API. A changed discriminator and an explicitly declared Self-Transition both create a new State Revision and activate matching Reactions. Returning `Delete` removes the current value using the same revision compare. Returning a State Value identical to the current value performs no etcd write, while retrying work without data changes uses `Retry`.

After reconciliation or reconnect, the current State Value is eligible for processing again because the runtime stores no durable reaction receipt.
