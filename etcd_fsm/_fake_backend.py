import asyncio
import math
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

from ._backend import (
    BackendClosed,
    BackendError,
    CompactedRevision,
    Compare,
    CompareOperator,
    CompareTarget,
    DeleteRange,
    DeleteResult,
    EtcdBackend,
    FutureRevision,
    KeyValue,
    Lease,
    LeaseID,
    LeaseNotFound,
    Put,
    PutResult,
    RangeRequest,
    RangeResult,
    Revision,
    TransactionOperation,
    TransactionResponse,
    TransactionResult,
    WatchBatch,
    WatchEvent,
    WatchEventKind,
)


@dataclass(frozen=True, slots=True)
class _Version:
    revision: Revision
    value: bytes | None
    create_revision: Revision
    version: int
    lease_id: LeaseID | None


@dataclass(slots=True)
class _LeaseState:
    ttl: int
    deadline: float


@dataclass(frozen=True, slots=True)
class _Watcher:
    start: bytes
    end: bytes | None
    queue: asyncio.Queue[WatchBatch | None]


class FakeEtcdBackend(EtcdBackend):
    """Deterministic in-memory MVCC backend used by core and runtime tests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._revision: Revision = 0
        self._compacted_revision: Revision = 0
        self._history: dict[bytes, list[_Version]] = {}
        self._events: list[WatchBatch] = []
        self._watchers: set[_Watcher] = set()
        self._leases: dict[LeaseID, _LeaseState] = {}
        self._next_lease_id: LeaseID = 1
        self._clock = 0.0
        self._closed = False

    @property
    def current_revision(self) -> Revision:
        return self._revision

    @property
    def compacted_revision(self) -> Revision:
        return self._compacted_revision

    async def connect(self) -> None:
        self._ensure_open()

    async def range(self, request: RangeRequest) -> RangeResult:
        async with self._lock:
            self._ensure_open()
            return self._range_unlocked(request)

    async def transaction(
        self,
        *,
        compares: Sequence[Compare],
        success: Sequence[TransactionOperation],
        failure: Sequence[TransactionOperation] = (),
    ) -> TransactionResult:
        async with self._lock:
            self._ensure_open()
            succeeded = all(self._compare_unlocked(compare) for compare in compares)
            operations = tuple(success if succeeded else failure)
            self._validate_transaction_unlocked(operations)

            changed = self._transaction_changes_data_unlocked(operations)
            revision = self._revision + 1 if changed else self._revision
            responses: list[TransactionResponse] = []
            events: list[WatchEvent] = []

            for operation in operations:
                if isinstance(operation, Put):
                    key_value = self._put_unlocked(operation, revision)
                    responses.append(PutResult(operation.key, revision))
                    events.append(WatchEvent(WatchEventKind.PUT, key_value))
                elif isinstance(operation, DeleteRange):
                    deleted, delete_events = self._delete_unlocked(operation, revision)
                    responses.append(DeleteResult(deleted))
                    events.extend(delete_events)
                else:
                    request = operation.request
                    if request.revision is not None:
                        raise BackendError(
                            "transactional GetRange cannot request a historical revision"
                        )
                    responses.append(
                        self._range_unlocked(
                            RangeRequest(
                                start=request.start,
                                end=request.end,
                                revision=self._revision or None,
                                limit=request.limit,
                            )
                        )
                    )

            if changed:
                self._revision = revision
                self._publish_unlocked(tuple(events), revision)

            return TransactionResult(
                succeeded=succeeded,
                responses=tuple(responses),
                revision=self._revision,
            )

    def watch(
        self,
        *,
        start: bytes,
        end: bytes | None = None,
        start_revision: Revision,
    ) -> AsyncGenerator[WatchBatch]:
        return self._watch(
            start=start,
            end=end,
            start_revision=start_revision,
        )

    async def grant_lease(self, ttl: int) -> Lease:
        if ttl <= 0:
            raise ValueError("lease TTL must be positive")

        async with self._lock:
            self._ensure_open()
            lease_id = self._next_lease_id
            self._next_lease_id += 1
            self._leases[lease_id] = _LeaseState(
                ttl=ttl,
                deadline=self._clock + ttl,
            )
            return Lease(id=lease_id, ttl=ttl)

    async def keep_alive(self, lease_id: LeaseID) -> int:
        async with self._lock:
            self._ensure_open()
            lease = self._leases.get(lease_id)
            if lease is None:
                raise LeaseNotFound(lease_id)
            lease.deadline = self._clock + lease.ttl
            return lease.ttl

    async def revoke_lease(self, lease_id: LeaseID) -> None:
        async with self._lock:
            self._ensure_open()
            if lease_id not in self._leases:
                raise LeaseNotFound(lease_id)
            self._leases.pop(lease_id)
            self._expire_lease_keys_unlocked(lease_id)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            for watcher in self._watchers:
                watcher.queue.put_nowait(None)
            self._watchers.clear()

    async def advance_time(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time cannot move backwards")

        async with self._lock:
            self._ensure_open()
            self._clock += seconds
            expired = [
                lease_id
                for lease_id, lease in self._leases.items()
                if lease.deadline <= self._clock
            ]
            for lease_id in expired:
                self._leases.pop(lease_id)
                self._expire_lease_keys_unlocked(lease_id)

    async def lease_time_to_live(self, lease_id: LeaseID) -> int:
        async with self._lock:
            self._ensure_open()
            lease = self._leases.get(lease_id)
            if lease is None:
                raise LeaseNotFound(lease_id)
            return max(0, math.ceil(lease.deadline - self._clock))

    async def compact(self, revision: Revision) -> None:
        async with self._lock:
            self._ensure_open()
            if revision > self._revision:
                raise FutureRevision(requested=revision, current=self._revision)
            if revision <= self._compacted_revision:
                return

            self._compacted_revision = revision
            self._events = [batch for batch in self._events if batch.revision > revision]
            for key, versions in self._history.items():
                baseline = [version for version in versions if version.revision <= revision]
                newer = [version for version in versions if version.revision > revision]
                self._history[key] = ([baseline[-1]] if baseline else []) + newer

    async def _watch(
        self,
        *,
        start: bytes,
        end: bytes | None,
        start_revision: Revision,
    ) -> AsyncGenerator[WatchBatch]:
        queue: asyncio.Queue[WatchBatch | None] = asyncio.Queue()
        watcher = _Watcher(start=start, end=end, queue=queue)

        async with self._lock:
            self._ensure_open()
            if self._compacted_revision and start_revision <= self._compacted_revision:
                raise CompactedRevision(
                    requested=start_revision,
                    compacted=self._compacted_revision,
                )
            backlog = [
                filtered
                for batch in self._events
                if batch.revision >= start_revision
                if (filtered := _filter_batch(batch, start, end)) is not None
            ]
            self._watchers.add(watcher)

        try:
            for batch in backlog:
                yield batch
            while True:
                batch = await queue.get()
                if batch is None:
                    return
                yield batch
        finally:
            async with self._lock:
                self._watchers.discard(watcher)

    def _range_unlocked(self, request: RangeRequest) -> RangeResult:
        if request.limit is not None and request.limit <= 0:
            raise ValueError("range limit must be positive")

        revision = request.revision
        if revision is None:
            revision = self._revision
        elif self._compacted_revision and revision <= self._compacted_revision:
            raise CompactedRevision(
                requested=revision,
                compacted=self._compacted_revision,
            )
        elif revision > self._revision:
            raise FutureRevision(requested=revision, current=self._revision)

        values = [
            value
            for key in sorted(self._history)
            if _key_in_range(key, request.start, request.end)
            if (value := self._key_value_at_unlocked(key, revision)) is not None
        ]

        more = request.limit is not None and len(values) > request.limit
        if request.limit is not None:
            values = values[: request.limit]
        next_key = values[-1].key + b"\0" if more else None
        return RangeResult(
            values=tuple(values),
            revision=revision,
            more=more,
            next_key=next_key,
        )

    def _compare_unlocked(self, compare: Compare) -> bool:
        current = self._key_value_at_unlocked(compare.key, self._revision)
        if compare.target is CompareTarget.VALUE:
            actual: bytes | int = current.value if current is not None else b""
        elif compare.target is CompareTarget.CREATE_REVISION:
            actual = current.create_revision if current is not None else 0
        elif compare.target is CompareTarget.MOD_REVISION:
            actual = current.mod_revision if current is not None else 0
        elif compare.target is CompareTarget.VERSION:
            actual = current.version if current is not None else 0
        else:
            actual = current.lease_id or 0 if current is not None else 0

        operand = compare.operand
        if isinstance(actual, bytes):
            if not isinstance(operand, bytes):
                raise BackendError(f"compare operand for {compare.target} has incompatible type")
            return _compare_bytes(actual, operand, compare.operator)
        if not isinstance(operand, int):
            raise BackendError(f"compare operand for {compare.target} has incompatible type")
        return _compare_ints(actual, operand, compare.operator) # pyright: ignore[reportArgumentType]

    def _validate_transaction_unlocked(
        self,
        operations: tuple[TransactionOperation, ...],
    ) -> None:
        write_ranges: list[tuple[bytes, bytes | None]] = []
        for operation in operations:
            if isinstance(operation, Put):
                if operation.lease_id is not None and operation.lease_id not in self._leases:
                    raise LeaseNotFound(operation.lease_id)
                candidate = (operation.key, operation.key + b"\0")
            elif isinstance(operation, DeleteRange):
                candidate = (operation.start, operation.end)
            else:
                continue

            if any(_ranges_overlap(candidate, existing) for existing in write_ranges):
                raise BackendError("transaction contains overlapping writes")
            write_ranges.append(candidate)

    def _transaction_changes_data_unlocked(
        self,
        operations: tuple[TransactionOperation, ...],
    ) -> bool:
        for operation in operations:
            if isinstance(operation, Put):
                return True
            if isinstance(operation, DeleteRange) and self._keys_in_range_unlocked(
                operation.start,
                operation.end,
            ):
                return True
        return False

    def _put_unlocked(self, operation: Put, revision: Revision) -> KeyValue:
        current = self._key_value_at_unlocked(operation.key, self._revision)
        create_revision = current.create_revision if current is not None else revision
        version = current.version + 1 if current is not None else 1
        stored = _Version(
            revision=revision,
            value=operation.value,
            create_revision=create_revision,
            version=version,
            lease_id=operation.lease_id,
        )
        self._history.setdefault(operation.key, []).append(stored)
        return _as_key_value(operation.key, stored)

    def _delete_unlocked(
        self,
        operation: DeleteRange,
        revision: Revision,
    ) -> tuple[int, tuple[WatchEvent, ...]]:
        keys = self._keys_in_range_unlocked(operation.start, operation.end)
        events: list[WatchEvent] = []
        for key in keys:
            current = self._key_value_at_unlocked(key, self._revision)
            assert current is not None
            deleted = _Version(
                revision=revision,
                value=None,
                create_revision=current.create_revision,
                version=current.version + 1,
                lease_id=None,
            )
            self._history[key].append(deleted)
            events.append(
                WatchEvent(
                    WatchEventKind.DELETE,
                    KeyValue(
                        key=key,
                        value=b"",
                        create_revision=current.create_revision,
                        mod_revision=revision,
                        version=current.version,
                        lease_id=None,
                    ),
                )
            )
        return len(keys), tuple(events)

    def _expire_lease_keys_unlocked(self, lease_id: LeaseID) -> None:
        keys = [
            key
            for key in self._history
            if (current := self._key_value_at_unlocked(key, self._revision)) is not None
            and current.lease_id == lease_id
        ]
        if not keys:
            return

        revision = self._revision + 1
        events: list[WatchEvent] = []
        for key in keys:
            _, deleted = self._delete_unlocked(DeleteRange(key), revision)
            events.extend(deleted)
        self._revision = revision
        self._publish_unlocked(tuple(events), revision)

    def _key_value_at_unlocked(
        self,
        key: bytes,
        revision: Revision,
    ) -> KeyValue | None:
        versions = self._history.get(key, ())
        version = next(
            (candidate for candidate in reversed(versions) if candidate.revision <= revision),
            None,
        )
        if version is None or version.value is None:
            return None
        return _as_key_value(key, version)

    def _keys_in_range_unlocked(
        self,
        start: bytes,
        end: bytes | None,
    ) -> list[bytes]:
        return [
            key
            for key in sorted(self._history)
            if _key_in_range(key, start, end)
            and self._key_value_at_unlocked(key, self._revision) is not None
        ]

    def _publish_unlocked(
        self,
        events: tuple[WatchEvent, ...],
        revision: Revision,
    ) -> None:
        if not events:
            return
        batch = WatchBatch(events=events, revision=revision)
        self._events.append(batch)
        for watcher in self._watchers:
            filtered = _filter_batch(batch, watcher.start, watcher.end)
            if filtered is not None:
                watcher.queue.put_nowait(filtered)

    def _ensure_open(self) -> None:
        if self._closed:
            raise BackendClosed("backend is closed")


def _as_key_value(key: bytes, version: _Version) -> KeyValue:
    assert version.value is not None
    return KeyValue(
        key=key,
        value=version.value,
        create_revision=version.create_revision,
        mod_revision=version.revision,
        version=version.version,
        lease_id=version.lease_id,
    )


def _key_in_range(key: bytes, start: bytes, end: bytes | None) -> bool:
    if end is None:
        return key == start
    if end == b"\0":
        return key >= start
    return start <= key < end


def _ranges_overlap(
    left: tuple[bytes, bytes | None],
    right: tuple[bytes, bytes | None],
) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    effective_left_end = left_start + b"\0" if left_end is None else left_end
    effective_right_end = right_start + b"\0" if right_end is None else right_end
    if effective_left_end == b"\0":
        return effective_right_end == b"\0" or effective_right_end > left_start
    if effective_right_end == b"\0":
        return effective_left_end > right_start
    return left_start < effective_right_end and right_start < effective_left_end


def _filter_batch(
    batch: WatchBatch,
    start: bytes,
    end: bytes | None,
) -> WatchBatch | None:
    events = tuple(
        event for event in batch.events if _key_in_range(event.key_value.key, start, end)
    )
    if not events:
        return None
    return WatchBatch(events=events, revision=batch.revision)


def _compare_bytes(
    actual: bytes,
    operand: bytes,
    operator: CompareOperator,
) -> bool:
    if operator is CompareOperator.EQUAL:
        return actual == operand
    if operator is CompareOperator.NOT_EQUAL:
        return actual != operand
    if operator is CompareOperator.GREATER:
        return actual > operand
    return actual < operand


def _compare_ints(
    actual: int,
    operand: int,
    operator: CompareOperator,
) -> bool:
    if operator is CompareOperator.EQUAL:
        return actual == operand
    if operator is CompareOperator.NOT_EQUAL:
        return actual != operand
    if operator is CompareOperator.GREATER:
        return actual > operand
    return actual < operand
