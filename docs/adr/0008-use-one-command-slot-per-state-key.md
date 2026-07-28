# Use one command slot per state key

Each State Key has one Command Slot that accepts a Command Model only when empty. Submission uses an atomic compare-and-put; a concurrent caller receives `Busy` instead of overwriting the pending command or silently creating a queue. This keeps command ordering and backpressure explicit while allowing consumers to discover pending work through watches and reconciliation.
