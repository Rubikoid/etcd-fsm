# Own the managed etcd namespace exclusively

State Values, command slots, claims, and retained results inside an FSM Schema's Managed Namespace are written only through the library's public client and runtime. Other services do not perform raw etcd writes into that keyspace. This makes signature-derived transitions, immutable keys, revision compares, command backpressure, and result publication enforceable invariants.

Administrative data repair is an offline operation performed with affected processors stopped and is outside the normal runtime API.
