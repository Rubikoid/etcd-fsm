# Reconcile a revision-pinned snapshot before watching

For each FSM Schema, a runtime reads a linearizable, revision-pinned snapshot of its Managed Namespace and then starts one shared watch at the following etcd revision. Paginated range reads remain pinned to the first response revision. Snapshot work is queued through the bounded scheduler, and claim acquisition compares the current modification revision so stale items cannot execute after a concurrent change.

On a temporary disconnect, the runtime resumes from its last confirmed watch revision. If that revision has been compacted, it performs a new full reconciliation instead of reconstructing missed intermediate states. One local schema watch fans out to all processors registered in that runtime.
