# Serialize transition producers per state revision

Transitioning Reactions and command functions acquire the same Transition Claim before invoking user code for a State Key and State Revision. Only the first claimant executes transition-producing work, while Observer Reactions remain independent. This prevents competing commands and automatic work from performing conflicting external actions before optimistic state commit selects a winner.

## Consequences

Acquisition is first-come-first-served with no default command priority. A producer releases the Transition Claim during retry backoff so other pending work can proceed; every eventual state commit still compares the original State Revision. External actions remain subject to at-least-once execution after lease loss and require idempotency.
