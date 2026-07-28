# Distributed FSM

This context describes shared finite-state machine schemas, independently deployed processors, and their typed state values.

## Language

**FSM Schema**:
A shared contract containing a storage namespace and the complete registries of State Models and Command Models used by independently deployed processors.
_Avoid_: FSM Definition, FSM

**Managed Namespace**:
The etcd keyspace owned exclusively by the library for one FSM Schema's states, commands, claims, and results.
_Avoid_: Shared application prefix

**FSM Runtime**:
The running environment that connects one or more FSM Processors to shared storage.
_Avoid_: FSM instance, state machine instance

**FSM Processor**:
An independently deployable, stably named collection of Reactions, commands, and initializers registered against an FSM Schema.
_Avoid_: Subscriber, FSM handler

**Bound FSM**:
A typed operational facade connecting one FSM Schema to an FSM Runtime.
_Avoid_: FSM instance

**Composed Graph**:
The complete transition graph obtained by combining the typed function signatures of all FSM Processors registered against one FSM Schema.
_Avoid_: Local graph

**State Model**:
A Pydantic model type representing one possible state and the data available in that state.
_Avoid_: FSM instance, state instance

**State Value**:
A validated instance of a State Model representing the current state and data of one tracked entity.
_Avoid_: FSM instance, current FSM

**State Key**:
An immutable composite path derived from the identity fields of a State Model or Command Model and locating one tracked entity's State Value.
_Avoid_: FSM instance ID

**State Discriminator**:
An enum member stored in every State Value that uniquely identifies its concrete State Model within an FSM Schema.
_Avoid_: State class name, type tag

**State Change Notification**:
A transient signal that a State Value may have changed. A consumer reconciles against the current State Value and is not guaranteed to observe every intermediate value.
_Avoid_: Event, transition event

**State Revision**:
A uniquely identified occurrence of a State Value created by initialization or a successful transition.
_Avoid_: Event ID

**Self-Transition**:
An explicitly allowed transition whose source and target have the same State Discriminator but different State Values.
_Avoid_: State update

**Quarantined State Key**:
A State Key whose stored data cannot be decoded as a valid State Value and is therefore excluded from processing.
_Avoid_: Invalid state

**Reaction**:
A named action associated with a State Model and executed while a matching State Revision is current.
_Avoid_: Callback, handler

**Observer Reaction**:
A Reaction that may perform work or request a retry but cannot change the current State Value.
_Avoid_: Read-only callback

**Transitioning Reaction**:
The sole Reaction selected for a concrete State Discriminator that may propose the next State Value.
_Avoid_: Writer callback, transition callback

**Reaction Outcome**:
A typed result instructing the runtime to transition to another State Value, retry the Reaction, or leave the current State Value unchanged.
_Avoid_: Callback return value

**Failed Reaction**:
A Reaction invocation stopped by an unexpected exception and held from immediate re-execution while its current Processing Claim remains alive.
_Avoid_: Rejected reaction, retry

**Deletion Outcome**:
The typed `Delete` result of a transition-producing function requesting removal of the current State Value.
_Avoid_: Direct delete

**Processing Claim**:
A temporary exclusive right to execute one Reaction for one State Revision.
_Avoid_: Lock

**Transition Claim**:
A temporary exclusive right to run any transition-producing function for one State Key and State Revision.
_Avoid_: State lock, transition lock

**Command Model**:
A Pydantic model carrying an enum discriminator, a State Key derived from its identity fields, and validated arguments for an externally requested operation.
_Avoid_: RPC payload, event

**Command Call**:
A correlated request that places a Command Model in a Command Slot and awaits its processing result.
_Avoid_: Fire-and-forget command

**Command Result**:
The typed completion value returned to the initiator of a Command Call: an applied State Value, a typed rejection, or an ignored result.
_Avoid_: RPC event

**Domain Rejection**:
An expected negative Command Result represented as `Rejected` with a typed user-defined error model.
_Avoid_: Exception, runtime error

**Command Handle**:
A stable reference to a durable Command Call that allows its initiator to resume waiting or request cancellation.
_Avoid_: Async task

**Command Slot**:
The single location associated with a State Key that may contain at most one pending Command Model.
_Avoid_: Command queue
