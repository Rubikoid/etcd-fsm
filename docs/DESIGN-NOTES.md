# Design Notes

Status: working design context, 2026-07-28.

This document records the premises and technical discoveries that shaped the
current design. It is intentionally different from the glossary in
[`CONTEXT.md`](../CONTEXT.md) and from the individual ADRs:

- `CONTEXT.md` defines the domain language.
- `docs/adr/` contains the authoritative decisions.
- This file explains why those decisions form one coherent design and lists
  the assumptions that must remain true for it to work.

If this file and an accepted ADR disagree, the ADR wins.

## Product Premises

### The FSM is a distributed current-state machine

The system stores one current state for each logical object in etcd. A state is
an immutable Pydantic model with an explicit enum discriminator.

State changes wake independently deployed subscribers. A subscriber may:

- observe the current state without changing it;
- return a new state and therefore perform a transition;
- explicitly retry later;
- leave the state unchanged;
- delete the logical object.

There is no public domain object called an `FSM Instance`. The current machine
instance is simply the State Value stored at a State Key. `FSM Runtime` is an
operational component, not the state itself.

### The system is not event sourced

Only the latest State Value is durable. etcd watch events are transient
wake-ups, not a durable business-event log.

Consequences:

- no transition history is retained by the library;
- intermediate states missed while a service is offline are not replayed;
- after reconnect or restart, a runtime scans the current snapshot and runs
  reactions that match what exists now;
- reactions match the current discriminator, not a `(previous, current)` pair;
- the same current state may be processed again after restart;
- the library does not provide exactly-once external effects.

This recovery behavior is deliberate. For example, a payment reaction remains
eligible while the order is in `PAYMENT_REQUIRED`. If charging succeeds, it
changes the state. If it fails transiently, it keeps retrying until it succeeds
or another coordinated operation changes the state.

See [ADR-0001](./adr/0001-store-only-current-state.md) and
[ADR-0033](./adr/0033-reconcile-snapshot-before-watching.md).

### Processors are independent services

Several independently deployed services may watch the same FSM Schema. Each
service owns an `FSM Processor` containing only its local reactions, commands,
and initializers. State transitions hand work from one service to another.

Example:

- a payments processor handles `PAYMENT_REQUIRED`;
- a delivery processor handles `PAID` and delivery states;
- both use the same Order schema and State Models;
- neither processor needs to contain all functions in the complete graph.

The Composed Graph is the union of all processor signatures. Local startup can
validate a processor fragment; a composition or integration test must validate
cross-service invariants.

See [ADR-0019](./adr/0019-compose-independently-deployed-processors.md).

### Schema rollouts are coordinated

The design does not support incompatible schema versions running against the
same namespace at the same time. Shared State Models and Command Models are a
deployment contract.

Schema changes must be backward-compatible with already stored values. A new
discriminator must not be written until every relevant processor understands
it. Incompatible data changes require an external migration while processors
are stopped.

This premise is why the storage format has no live schema-version envelope or
runtime migration framework.

See [ADR-0013](./adr/0013-require-backward-compatible-state-schemas.md) and
[ADR-0021](./adr/0021-coordinate-schema-rollouts.md).

### The library owns its etcd namespace

All writes in a managed namespace go through the library. There are no
uncoordinated raw etcd writers. Offline repair is allowed only while processors
are stopped.

This premise makes revision checks, claims, command slots, and state validation
sufficient to enforce the FSM rules.

See [ADR-0029](./adr/0029-own-the-managed-etcd-namespace.md).

## State Model

### One enum is the source of truth

Every concrete State Model in one FSM Schema uses the same enum type. The
discriminator value, rather than `isinstance(value, SomeState)`, determines the
concrete state.

Schema finalization must verify at runtime that:

- all concrete states use the schema's enum;
- every enum member maps to exactly one concrete State Model;
- no enum member is missing;
- no enum member is registered twice;
- each discriminator annotation and default agree.

Intermediate generic base classes are not concrete states and are excluded
from completeness checks.

The explicit declaration pattern is:

```python
class OrderState[S: OrderStatus](BaseState[S]):
    ...


class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED
```

This duplication inside a model is intentional: the literal gives static
precision, while the default gives ergonomic Pydantic construction and an
unambiguous serialized discriminator.

See [ADR-0012](./adr/0012-declare-state-discriminators-as-literals.md).

### State Values are immutable snapshots

A mutation is a new State Value, represented by a new model object. There is no
generic in-place update operation.

State models should be frozen. Pydantic freezing is shallow, so runtime
boundaries must still defend against mutation of nested mutable values:

- decode a separate model for each invocation;
- capture a canonical pre-invocation representation;
- compare it after the handler returns;
- revalidate every returned State Value before committing it.

Unexpected mutation is a handler failure, not a state transition.

See [ADR-0024](./adr/0024-treat-models-as-immutable-snapshots.md) and
[ADR-0018](./adr/0018-change-state-values-only-through-transitions.md).

### Models derive their own keys

State and Command models implement a structural protocol with an instance
method such as:

```python
class FSMKey(Protocol):
    def fsm_key(self) -> str: ...
```

The key may be composite and human-readable. Identity fields may also remain
inside the model:

```text
dtc/{user_id}/{task_id}
```

Slashes are useful for debugging and prefix scans, so they remain supported.
Keys are normalized relative paths. Empty segments, absolute paths, `.`, `..`,
and the reserved `rpc` segment are invalid.

A transition must preserve the current key. A command is invoked with a
concrete Command Model, so it can derive the target key from its own fields.

This supersedes the original external-key decision in ADR-0005. See
[ADR-0016](./adr/0016-derive-composite-keys-from-models.md).

## Graph And Dispatch

### Function signatures are the source of truth

The API must not duplicate transition declarations in calls such as
`.allow(Source, Target)`. The runtime/compiler derives the graph from resolved
function annotations using `typing.get_type_hints(..., include_extras=True)`.

For a transitioning reaction:

- the state parameter determines the source states;
- the returned State Model union determines possible target states;
- operational outcomes such as `Retry`, `None`, and `Delete` do not add state
  edges.

Initializer and command signatures contribute their own graph edges. Ambiguous
or unsupported annotations are schema/processor finalization errors.

This makes annotations an executable contract shared by static analysis,
runtime dispatch, and graph inspection.

See [ADR-0007](./adr/0007-derive-transition-graph-from-signatures.md).

### Reactions match discriminators

Exact matching is the default. A reaction may optionally declare
`SUBCLASSES` matching:

1. resolve the current discriminator to its registered concrete State Model;
2. inspect the registered model hierarchy;
3. match a handler declared for a base state.

The option should not be called `final`: that word is too easily confused with
a terminal FSM state.

Any number of observer reactions may match one state. Across the complete
Composed Graph, at most one transitioning reaction may own a concrete state.

See [ADR-0003](./adr/0003-match-reactions-from-state-discriminators.md) and
[ADR-0006](./adr/0006-allow-one-transitioning-reaction-per-state.md).

### Self-transitions are real transitions

A handler may return the same concrete state type when its signature declares
that target. If the returned value differs, runtime commits a new revision and
watchers may process it again.

If the returned value is identical to the current value, runtime performs no
PUT. A handler that needs another attempt without changing data returns
`Retry`; it must not manufacture a meaningless revision.

## Reaction Execution

### Delivery is at least once

A Processing Claim prevents replicas of the same logical reaction from running
concurrently for one State Key and State Revision. Claims are leased, not
durable receipts.

If a runtime loses its lease or restarts, the current state may be processed
again. External side effects therefore require idempotency. A stable reaction
idempotency key can include:

```text
schema / state-key / state-revision / processor / reaction
```

A stable processor name is required. A reaction `name=` remains optional: by
default it is derived from the Python function, while an explicit name
preserves idempotency identity across function renames.

See [ADR-0002](./adr/0002-at-least-once-reactions-with-claims.md).

### Transition producers are serialized

An automatic reaction and a command may both produce a transition from the
same current revision. A shared Transition Claim serializes those side-effecting
producers:

- acquire local capacity before a distributed claim;
- acquire the Transition Claim before invoking user side effects;
- commit only if the state revision and claim ownership still match;
- the first producer to acquire the claim wins;
- observer reactions do not need this transition claim;
- release it while waiting for an explicit retry backoff.

See [ADR-0010](./adr/0010-serialize-transition-producers.md).

### Outcomes distinguish expected control flow from failures

A transitioning reaction conceptually returns:

```text
NextState | Retry | None | Delete
```

- `NextState` requests a validated transition.
- `Retry` is an expected transient outcome.
- `None` leaves the state unchanged and retains ownership while that runtime
  remains alive.
- `Delete` removes the complete logical object.

Expected domain-negative command results are typed values, normally
`Rejected[DomainError]`. Programming errors, invariant violations, storage
failures, and other exceptional failures are exceptions.

An unexpected reaction exception is contained by the runtime, reported through
the error/health surface, and keeps the Processing Claim to avoid a hot crash
loop. A later lease loss or restart can make it eligible again. An unexpected
command exception completes that command with `CommandExecutionError` and frees
the command slot.

See [ADR-0004](./adr/0004-use-typed-reaction-outcomes.md) and
[ADR-0026](./adr/0026-separate-domain-results-from-runtime-exceptions.md).

### Retries are bounded in time, not in attempts

Handlers have configurable timeouts. Retries use exponential backoff with
jitter and have no default maximum attempt count. Per-function overrides are
allowed, and `Retry` may request a minimum delay.

On timeout, runtime requests cooperative cancellation and does not start
another attempt until the timed-out coroutine has actually stopped. If user
code suppresses cancellation, the invocation is marked stuck, its claims remain
held, and health is degraded.

The scheduler has global and per-function concurrency limits. It deduplicates
queued work by schema, key, revision, and function; stale revisions are dropped.

See [ADR-0028](./adr/0028-bound-retries-with-timeouts-and-backoff.md) and
[ADR-0032](./adr/0032-bound-runtime-handler-concurrency.md).

## Commands

### Commands are typed durable calls, not transient messages

A Command Model is a Pydantic model containing:

- an enum discriminator;
- typed command arguments;
- enough identity fields to derive the target State Key.

There is one pending command slot at `{state-key}/rpc`. Submission uses an
atomic compare-and-put. Concurrent submission to an occupied slot returns
`Busy`; commands are not queued.

A successfully submitted call has a stable `call_id` and returns a
`CommandHandle[R]`. A caller timeout stops only the local wait. The durable call
remains pending and the handle can resume waiting or request cancellation.

See [ADR-0008](./adr/0008-use-one-command-slot-per-state-key.md),
[ADR-0015](./adr/0015-command-calls-outlive-callers.md), and
[ADR-0017](./adr/0017-separate-pending-command-from-results.md).

### Results are typed through the Command Model

The caller and handler may live in separate services, so the result type must
be available from their shared Command Model rather than inferred only from the
handler.

Conceptually:

```python
type CancelOrderResult = (
    Applied[Cancelled]
    | Rejected[CannotCancelOrder]
    | Ignored
    | Delete
)


class CancelOrder(Command[CancelOrderResult]):
    ...
```

The handler may additionally return `Retry`; `Retry` is runtime control flow
and is not returned by `CommandHandle.result()`.

Expected domain results are ordinary values. Missing dispatch, cancellation,
expiry, storage errors, and execution failures remain typed exceptions. `Busy`
occurs before a handle exists.

See [ADR-0009](./adr/0009-return-results-for-command-calls.md),
[ADR-0025](./adr/0025-type-command-results-through-model-generics.md), and
[ADR-0026](./adr/0026-separate-domain-results-from-runtime-exceptions.md).

### Initialization is a command

There is no unsafe `create(any_state)` operation. A typed initializer command:

- has no current State Value;
- declares its possible initial states through `Applied[State]`;
- atomically asserts that the target state is absent;
- validates that the returned state derives the same key as the command.

See [ADR-0020](./adr/0020-initialize-state-through-commands.md).

### Cancellation is cooperative

Cancellation uses a transaction that compares the call identity/revision,
removes the pending slot, and records `CommandCancelled`.

A late handler cannot commit state because its compare-and-swap no longer
matches. Already completed external side effects cannot be rolled back. If
normal completion wins the race, cancellation returns the completed result.

See [ADR-0031](./adr/0031-cancel-command-calls-cooperatively.md).

## Deletion

`Delete` removes the entire object-owned subtree:

```text
{state-key}
{state-key}/rpc
{state-key}/rpc/...
```

There is no tombstone and the library does not distinguish "never existed"
from "was deleted". Technically the key can later be initialized again; the
application's UUID/identity policy is expected to make accidental reuse
meaningless.

A delete command still needs a resumable result. Its short-lived result is
therefore written outside the object subtree in a runtime-owned result
namespace with a TTL. This is operational correlation data, not state history.

See [ADR-0022](./adr/0022-delete-state-through-typed-outcomes.md) and
[ADR-0023](./adr/0023-retain-delete-results-outside-object-subtrees.md).

## Runtime

### Runtime is supervised infrastructure

`FSM Runtime` is started as a collection of async coroutines inside a larger
service. It does not own the event loop, signal handling, or process lifecycle.

After successful configuration/finalization, ordinary runtime failures must not
bring down `run()`:

- recoverable `Exception` instances are contained and reported;
- failure of one internal task does not cancel unrelated tasks;
- watch, connection, lease, and retry loops restart with backoff;
- a failing error hook is itself isolated;
- external `CancelledError` is the explicit shutdown path and propagates after
  cleanup.

Configuration errors may fail fast before the runtime starts. The guarantee
cannot cover process termination, `MemoryError`, a broken event loop, or other
fatal `BaseException` conditions.

See [ADR-0027](./adr/0027-supervise-runtime-tasks-without-failure-propagation.md).

### Every command sender runs a runtime

There is no separate lightweight command client. A service that sends commands
also runs the runtime and obtains a typed Bound FSM facade from it.

One runtime may host processors for multiple schemas over one etcd connection.

See [ADR-0030](./adr/0030-expose-operations-through-the-runtime.md). ADR-0011
records the earlier terminology that was superseded by the Schema/Processor
split.

### One runtime session owns leased claims

One session lease per runtime instance owns Processing Claims and Transition
Claims. Lease loss:

- stops new claim acquisition;
- cancels owned invocations;
- prevents stale commits through transactional ownership checks;
- starts reconnection and reconciliation.

Session TTL and keepalive intervals are configurable.

See [ADR-0034](./adr/0034-attach-claims-to-one-runtime-session-lease.md).

## Etcd Correctness

### Reconciliation must close the scan/watch gap

Startup and recovery use this algorithm:

1. perform a linearizable range scan and record revision `R`;
2. pin every pagination request to `R`;
3. schedule work from that snapshot;
4. start watching at `R + 1`;
5. compare the current `mod_revision` before claims and commits.

After an ordinary disconnect, resume from the last observed revision. If etcd
has compacted that revision, perform a new full reconciliation; do not pretend
to replay missing business history.

One runtime uses one watch per schema and fans events out to its local
processors.

### `aetcd` is viable but needs a narrow adapter

The original project used `aetcd`, and its async API exposes the primitives the
design needs:

- transactions and compare-and-swap;
- watches with `start_revision`;
- prefix watches;
- leases;
- range/prefix deletion;
- response headers and modification revisions.

The important source-level discovery is that the inspected `aetcd` public range
helpers do not expose all parameters needed for revision-pinned pagination.
The internal range-request builder accepts related parameters but does not
populate all of them.

This needs one internal storage boundary with two implementations:

```text
FSM Runtime
    |
    +-- EtcdBackend
            +-- aetcd-backed production implementation
            +-- deterministic fake used by tests
```

`EtcdBackend` is an internal interface, not another user-facing FSM concept.
The production implementation is an adapter around `aetcd`, not a separate
architectural layer. It uses narrow raw protobuf requests for revision-pinned
Range calls and transactions. Raw Txn is required because the public
`aetcd.transaction()` result omits the response header revision and collapses
some response details needed by the backend contract. Watches and leases use
the public `aetcd` API. Backend contract tests must cover this mixed
implementation explicitly.

TLS is not required for the initial supported deployment. It may be added to
the production backend later without changing the FSM API.

## Invalid Data

Invalid data at one known key must not crash the runtime or stop processing
other objects.

On decode/validation failure:

- quarantine that key from normal dispatch;
- preserve the raw value and validation details for the error hook;
- expose the condition through health/metrics;
- make direct operations on that object fail explicitly;
- continue processing other keys.

An unknown discriminator is treated as invalid deployment/data under the
coordinated-rollout premise, not as a signal to wait indefinitely for a newer
binary.

See [ADR-0014](./adr/0014-isolate-invalid-state-keys.md).

## Technical Discoveries

The following observations were checked while shaping the design and should be
turned into regression tests:

1. A mutable field override is invariant to Python type checkers. A
   non-generic `OrderState(BaseState[OrderStatus])` cannot safely narrow
   `state` to `Literal[OrderStatus.CREATED]`.
2. A generic intermediate
   `OrderState[S: OrderStatus](BaseState[S])` preserves the literal and lets
   Pyright/BasedPyright reject a state from another enum.
3. Pydantic emits the literal discriminator as a constant in the schema, and a
   default allows construction without manually passing the discriminator.
4. Pydantic frozen models do not recursively freeze nested containers.
5. etcd watches alone cannot provide lossless recovery; a revision-pinned
   snapshot followed by a watch is mandatory.
6. Leased exclusion prevents concurrent duplicate execution but cannot turn
   external side effects into exactly-once effects.
7. A command result stored below an object cannot survive deletion of the whole
   object, hence delete results need a separate temporary namespace.
8. Variable-depth composite keys make deletion boundaries and prefix scans an
   explicit backend/API concern, not mere string concatenation.

## Non-Goals

The library is not intended to provide:

- event sourcing, transition history, or missed-state replay;
- exactly-once external side effects;
- direct raw writes into the managed namespace;
- mutable State Values;
- a generic update operation that bypasses the transition graph;
- direct deletion that bypasses a typed outcome;
- live schema migrations or incompatible mixed-version rollouts;
- a lightweight command client without a runtime;
- more than one queued pending command per object;
- permanent deletion tombstones;
- ownership of the process event loop or OS signals.

## Open Questions

These points are not design commitments yet:

- What are the defaults for handler timeout, retry backoff, concurrency,
  session TTL/keepalive, and command-result retention?
- What is the exact control-key layout for claims and external delete results?
- Should all completed command results move to one result namespace, or should
  only delete results live outside `{state-key}/rpc`?
- What is the public error hook, health, logging, and metrics API?
- How are processor manifests composed across separately packaged services in
  CI?
- What exact syntax will the public decorators/builders use?
- What guarantees and validation apply to prefix relationships between
  variable-depth composite keys?
- What complete annotation grammar will the signature compiler support,
  especially for inherited generic handlers?
- Which deterministic fake-backend scenarios and real-etcd failure/compaction
  tests are required for the first release?

## Suggested Implementation Order

1. Implement core types, FSM Schema compilation, signature analysis, and the
   Order example without etcd.
2. Define `EtcdBackend`, build a deterministic fake, and write backend contract
   tests.
3. Implement `AetcdBackend`, revision-safe reconciliation, watches, and the
   runtime session lease.
4. Add reaction scheduling, claims, retries, and transactional transitions.
5. Add durable commands, typed results, cancellation, initialization, and
   deletion.
6. Validate multi-processor composition with end-to-end and failure-injection
   tests.
7. Stabilize the public API and add observability and operational
   documentation.

## Order Scenario For Tests

The initial integration domain should use explicit states such as:

```text
CREATED
PAYMENT_REQUIRED
PAID
PAYMENT_FAILED
HANDED_TO_DELIVERY
DELIVERED
CANCELLED
```

Representative edges:

```text
CREATED -> PAYMENT_REQUIRED
PAYMENT_REQUIRED -> PAID | PAYMENT_FAILED
PAYMENT_FAILED -> PAYMENT_REQUIRED
PAID -> HANDED_TO_DELIVERY
HANDED_TO_DELIVERY -> DELIVERED
... -> CANCELLED where the domain permits it
```

The test suite should split these handlers across at least two processors and
cover:

- signature-derived branching and invalid enum declarations;
- handoff between independently deployed processors;
- two replicas competing for the same reaction;
- reaction restart and reconnect reconciliation;
- lease loss during handler execution;
- command-slot `Busy`;
- command timeout and resumable handles;
- completion/cancellation races;
- self-transitions and identical-value returns;
- transient retries and stuck cancellation-resistant handlers;
- full-subtree `Delete` with a retrievable temporary result;
- idempotency keys for repeated current-state processing.
