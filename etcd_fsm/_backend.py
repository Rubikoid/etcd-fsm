from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

type Revision = int
type LeaseID = int


class CompareTarget(StrEnum):
    VALUE = "value"
    CREATE_REVISION = "create_revision"
    MOD_REVISION = "mod_revision"
    VERSION = "version"
    LEASE = "lease"


class CompareOperator(StrEnum):
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    GREATER = "greater"
    LESS = "less"


class WatchEventKind(StrEnum):
    PUT = "put"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class KeyValue:
    key: bytes
    value: bytes
    create_revision: Revision
    mod_revision: Revision
    version: int
    lease_id: LeaseID | None


@dataclass(frozen=True, slots=True)
class RangeRequest:
    start: bytes
    end: bytes | None = None
    revision: Revision | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class RangeResult:
    values: tuple[KeyValue, ...]
    revision: Revision
    more: bool
    next_key: bytes | None


@dataclass(frozen=True, slots=True)
class Compare:
    key: bytes
    target: CompareTarget
    operator: CompareOperator
    operand: bytes | int


@dataclass(frozen=True, slots=True)
class Put:
    key: bytes
    value: bytes
    lease_id: LeaseID | None = None


@dataclass(frozen=True, slots=True)
class DeleteRange:
    start: bytes
    end: bytes | None = None


@dataclass(frozen=True, slots=True)
class GetRange:
    request: RangeRequest


type TransactionOperation = Put | DeleteRange | GetRange


@dataclass(frozen=True, slots=True)
class PutResult:
    key: bytes
    revision: Revision


@dataclass(frozen=True, slots=True)
class DeleteResult:
    deleted: int


type TransactionResponse = PutResult | DeleteResult | RangeResult


@dataclass(frozen=True, slots=True)
class TransactionResult:
    succeeded: bool
    responses: tuple[TransactionResponse, ...]
    revision: Revision


@dataclass(frozen=True, slots=True)
class WatchEvent:
    kind: WatchEventKind
    key_value: KeyValue


@dataclass(frozen=True, slots=True)
class WatchBatch:
    events: tuple[WatchEvent, ...]
    revision: Revision


@dataclass(frozen=True, slots=True)
class Lease:
    id: LeaseID
    ttl: int


class BackendError(Exception):
    """Base error for failures at the internal etcd boundary."""


class BackendClosed(BackendError):
    """The backend has been closed."""


class LeaseNotFound(BackendError):
    def __init__(self, lease_id: LeaseID) -> None:
        self.lease_id = lease_id
        super().__init__(f"lease {lease_id} does not exist")


class CompactedRevision(BackendError):
    def __init__(self, *, requested: Revision, compacted: Revision) -> None:
        self.requested = requested
        self.compacted = compacted
        super().__init__(f"revision {requested} has been compacted at revision {compacted}")


class FutureRevision(BackendError):
    def __init__(self, *, requested: Revision, current: Revision) -> None:
        self.requested = requested
        self.current = current
        super().__init__(f"revision {requested} is newer than current revision {current}")


class EtcdBackend(Protocol):
    async def connect(self) -> None:
        """Establish the backend connection."""
        ...

    async def range(self, request: RangeRequest) -> RangeResult:
        """Read one current or revision-pinned range page."""
        ...

    async def transaction(
        self,
        *,
        compares: Sequence[Compare],
        success: Sequence[TransactionOperation],
        failure: Sequence[TransactionOperation] = (),
    ) -> TransactionResult:
        """Atomically evaluate compares and execute one operation branch."""
        ...

    def watch(
        self,
        *,
        start: bytes,
        end: bytes | None = None,
        start_revision: Revision,
    ) -> AsyncIterator[WatchBatch]:
        """Watch a range starting at an exact etcd revision."""
        ...

    async def grant_lease(self, ttl: int) -> Lease:
        """Create a lease used by one runtime session."""
        ...

    async def keep_alive(self, lease_id: LeaseID) -> int:
        """Refresh a lease and return its remaining TTL."""
        ...

    async def revoke_lease(self, lease_id: LeaseID) -> None:
        """Revoke a lease and delete all keys attached to it."""
        ...

    async def close(self) -> None:
        """Release backend resources and stop active watches."""
        ...


def prefix_range_end(prefix: bytes) -> bytes:
    if not prefix:
        return b"\0"

    candidate = bytearray(prefix)
    for index in range(len(candidate) - 1, -1, -1):
        if candidate[index] < 0xFF:
            candidate[index] += 1
            return bytes(candidate[: index + 1])
    return b"\0"


def prefix_range(
    prefix: bytes, *, revision: Revision | None = None, limit: int | None = None
) -> RangeRequest:
    return RangeRequest(
        start=prefix,
        end=prefix_range_end(prefix),
        revision=revision,
        limit=limit,
    )
