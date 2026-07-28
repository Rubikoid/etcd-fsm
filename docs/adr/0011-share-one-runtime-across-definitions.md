---
status: superseded by ADR-0019
---

# Share one runtime across FSM definitions

An FSM Runtime owns the etcd connection, watches, reconciliation, leases, claims, and retry scheduling for multiple FSM Definitions. Definitions remain connection-free declarations, while a Bound FSM provides a typed operational facade for one definition and its namespace. This shares infrastructure lifecycle without introducing a domain-level FSM instance object.
