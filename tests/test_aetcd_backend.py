# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
from collections.abc import AsyncIterator, Coroutine
from types import SimpleNamespace
from typing import Any

import pytest
from aetcd import exceptions as aetcd_exceptions
from aetcd import rpc, rtypes

from etcd_fsm._aetcd_backend import AetcdBackend
from etcd_fsm._backend import (
    CompactedRevision,
    Compare,
    CompareOperator,
    CompareTarget,
    DeleteRange,
    DeleteResult,
    EtcdBackend,
    GetRange,
    Put,
    PutResult,
    RangeRequest,
    RangeResult,
    WatchEventKind,
)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class StubKV:
    def __init__(self) -> None:
        self.range_response: Any = None
        self.transaction_response: Any = None
        self.range_request: Any = None
        self.transaction_request: Any = None

    async def Range(self, request: Any, **kwargs: Any) -> Any:
        _ = kwargs
        self.range_request = request
        if isinstance(self.range_response, Exception):
            raise self.range_response
        return self.range_response

    async def Txn(self, request: Any, **kwargs: Any) -> Any:
        _ = kwargs
        self.transaction_request = request
        if isinstance(self.transaction_response, Exception):
            raise self.transaction_response
        return self.transaction_response


class StubWatch:
    def __init__(self, events: tuple[Any, ...]) -> None:
        self.events = events
        self.cancelled = False

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def _events(self) -> AsyncIterator[Any]:
        for event in self.events:
            yield event

    async def cancel(self) -> None:
        self.cancelled = True


class StubClient:
    def __init__(self) -> None:
        self.kvstub = StubKV()
        self._timeout = 3
        self.metadata = None
        self.connected = False
        self.closed = False
        self.watch_result: StubWatch | Exception | None = None
        self.revoked_lease: int | None = None

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def watch(self, *args: Any, **kwargs: Any) -> StubWatch:
        _ = args, kwargs
        if isinstance(self.watch_result, Exception):
            raise self.watch_result
        assert self.watch_result is not None
        return self.watch_result

    async def lease(self, ttl: int) -> Any:
        return SimpleNamespace(id=11, ttl=ttl)

    async def refresh_lease(self, lease_id: int) -> Any:
        _ = lease_id
        return SimpleNamespace(TTL=7)

    async def revoke_lease(self, lease_id: int) -> None:
        self.revoked_lease = lease_id


def kv(
    key: bytes,
    value: bytes,
    *,
    create_revision: int = 1,
    mod_revision: int = 1,
    version: int = 1,
    lease: int = 0,
) -> Any:
    return rpc.KeyValue(
        key=key,
        value=value,
        create_revision=create_revision,
        mod_revision=mod_revision,
        version=version,
        lease=lease,
    )


def test_aetcd_backend_satisfies_protocol_statically() -> None:
    client = StubClient()
    backend: EtcdBackend = AetcdBackend(client=client)

    run(backend.connect())
    assert client.connected


def test_range_uses_raw_revision_and_limit_fields() -> None:
    async def scenario() -> None:
        client = StubClient()
        client.kvstub.range_response = rpc.RangeResponse(
            header=rpc.ResponseHeader(revision=12),
            kvs=[kv(b"/state/a", b"a", mod_revision=7)],
            more=True,
            count=2,
        )
        backend = AetcdBackend(client=client)

        result = await backend.range(
            RangeRequest(
                start=b"/state/",
                end=b"/state0",
                revision=7,
                limit=1,
            )
        )

        request = client.kvstub.range_request
        assert request.revision == 7
        assert request.limit == 1
        assert request.sort_order == rpc.RangeRequest.ASCEND
        assert result.revision == 7
        assert result.next_key == b"/state/a\0"
        assert result.values[0].lease_id is None

    run(scenario())


def test_transaction_uses_raw_compare_and_preserves_header_revision() -> None:
    async def scenario() -> None:
        client = StubClient()
        client.kvstub.transaction_response = rpc.TxnResponse(
            header=rpc.ResponseHeader(revision=9),
            succeeded=True,
            responses=[
                rpc.ResponseOp(response_put=rpc.PutResponse()),
                rpc.ResponseOp(
                    response_range=rpc.RangeResponse(
                        kvs=[kv(b"/state/a", b"next", mod_revision=9)],
                        more=False,
                        count=1,
                    )
                ),
                rpc.ResponseOp(response_delete_range=rpc.DeleteRangeResponse(deleted=2)),
            ],
        )
        backend = AetcdBackend(client=client)

        result = await backend.transaction(
            compares=(
                Compare(
                    key=b"/state/a",
                    target=CompareTarget.MOD_REVISION,
                    operator=CompareOperator.EQUAL,
                    operand=8,
                ),
            ),
            success=(
                Put(b"/state/a", b"next", lease_id=11),
                GetRange(RangeRequest(b"/state/a")),
                DeleteRange(b"/claims/"),
            ),
        )

        request = client.kvstub.transaction_request
        assert request.compare[0].target == rpc.Compare.MOD
        assert request.compare[0].mod_revision == 8
        assert request.success[0].request_put.lease == 11
        assert result.revision == 9
        assert isinstance(result.responses[0], PutResult)
        assert isinstance(result.responses[1], RangeResult)
        assert isinstance(result.responses[2], DeleteResult)

    run(scenario())


def test_watch_converts_events_and_cancels_aetcd_watch() -> None:
    async def scenario() -> None:
        client = StubClient()
        event = rtypes.Event(
            rpc.Event.PUT,
            kv(b"/state/a", b"value", mod_revision=4),
        )
        watch = StubWatch((event,))
        client.watch_result = watch
        backend = AetcdBackend(client=client)

        iterator = backend.watch(
            start=b"/state/",
            end=b"/state0",
            start_revision=4,
        )
        batch = await anext(iterator)
        await iterator.aclose()

        assert batch.revision == 4
        assert batch.events[0].kind is WatchEventKind.PUT
        assert batch.events[0].key_value.value == b"value"
        assert watch.cancelled

    run(scenario())


def test_watch_compaction_is_translated() -> None:
    async def scenario() -> None:
        client = StubClient()
        client.watch_result = aetcd_exceptions.RevisionCompactedError(5)
        backend = AetcdBackend(client=client)
        iterator = backend.watch(
            start=b"/state/",
            end=b"/state0",
            start_revision=3,
        )

        with pytest.raises(CompactedRevision) as captured:
            await anext(iterator)

        assert captured.value.requested == 3
        assert captured.value.compacted == 5

    run(scenario())


def test_lease_operations_use_public_aetcd_api() -> None:
    async def scenario() -> None:
        client = StubClient()
        backend = AetcdBackend(client=client)

        lease = await backend.grant_lease(7)
        remaining = await backend.keep_alive(lease.id)
        await backend.revoke_lease(lease.id)
        await backend.close()

        assert lease.id == 11
        assert lease.ttl == 7
        assert remaining == 7
        assert client.revoked_lease == 11
        assert client.closed

    run(scenario())
