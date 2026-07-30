import asyncio
import os
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any
from uuid import uuid4

import pytest

from etcd_fsm._aetcd_backend import AetcdBackend
from etcd_fsm._backend import (
    Compare,
    CompareOperator,
    CompareTarget,
    DeleteRange,
    GetRange,
    Put,
    PutResult,
    RangeRequest,
    RangeResult,
    WatchEventKind,
    prefix_range,
    prefix_range_end,
)

pytestmark = pytest.mark.integration


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


@asynccontextmanager
async def real_backend() -> AsyncGenerator[tuple[AetcdBackend, bytes]]:
    host = os.getenv("ETCD_FSM_TEST_HOST", "127.0.0.1")
    port = int(os.getenv("ETCD_FSM_TEST_PORT", "12379"))
    backend = AetcdBackend(
        host=host,
        port=port,
        timeout=5,
        options={"grpc.enable_http_proxy": 0},
    )
    prefix = f"/etcd-fsm-tests/{uuid4().hex}/".encode()
    await backend.connect()
    try:
        yield backend, prefix
    finally:
        with suppress(Exception):
            await backend.transaction(
                compares=(),
                success=(DeleteRange(prefix, prefix_range_end(prefix)),),
            )
        await backend.close()


def test_real_etcd_compare_and_put_is_atomic() -> None:
    async def scenario() -> None:
        async with real_backend() as (backend, prefix):
            key = prefix + b"state"
            compare_missing = Compare(
                key=key,
                target=CompareTarget.CREATE_REVISION,
                operator=CompareOperator.EQUAL,
                operand=0,
            )

            created = await backend.transaction(
                compares=(compare_missing,),
                success=(Put(key, b"created"),),
            )
            busy = await backend.transaction(
                compares=(compare_missing,),
                success=(Put(key, b"overwritten"),),
                failure=(GetRange(RangeRequest(key)),),
            )

            assert created.succeeded
            assert isinstance(created.responses[0], PutResult)
            assert not busy.succeeded
            assert isinstance(busy.responses[0], RangeResult)
            assert busy.responses[0].values[0].value == b"created"
            current = await backend.range(RangeRequest(key))
            assert current.values[0].value == b"created"

    run(scenario())


def test_real_etcd_range_pagination_stays_on_pinned_revision() -> None:
    async def scenario() -> None:
        async with real_backend() as (backend, prefix):
            end = prefix_range_end(prefix)
            await backend.transaction(
                compares=(),
                success=(
                    Put(prefix + b"a", b"a-v1"),
                    Put(prefix + b"b", b"b-v1"),
                    Put(prefix + b"c", b"c-v1"),
                ),
            )

            first = await backend.range(prefix_range(prefix, limit=1))
            assert first.more
            assert first.next_key is not None
            assert first.values[0].value == b"a-v1"

            await backend.transaction(
                compares=(),
                success=(
                    Put(prefix + b"b", b"b-v2"),
                    DeleteRange(prefix + b"c"),
                ),
            )

            second = await backend.range(
                RangeRequest(
                    start=first.next_key,
                    end=end,
                    revision=first.revision,
                    limit=1,
                )
            )
            assert second.values[0].value == b"b-v1"
            assert second.more
            assert second.next_key is not None

            third = await backend.range(
                RangeRequest(
                    start=second.next_key,
                    end=end,
                    revision=first.revision,
                    limit=1,
                )
            )
            assert third.values[0].value == b"c-v1"
            assert not third.more

    run(scenario())


def test_real_etcd_watch_replays_history_and_streams_live_changes() -> None:
    async def scenario() -> None:
        async with real_backend() as (backend, prefix):
            historical_key = prefix + b"historical"
            live_key = prefix + b"live"
            created = await backend.transaction(
                compares=(),
                success=(Put(historical_key, b"one"),),
            )

            watch = backend.watch(
                start=prefix,
                end=prefix_range_end(prefix),
                start_revision=created.revision,
            )
            try:
                historical = await asyncio.wait_for(anext(watch), timeout=5)
                assert historical.revision == created.revision
                assert historical.events[0].kind is WatchEventKind.PUT
                assert historical.events[0].key_value.key == historical_key

                pending = asyncio.create_task(anext(watch))
                await asyncio.sleep(0)
                changed = await backend.transaction(
                    compares=(),
                    success=(Put(live_key, b"two"),),
                )
                live = await asyncio.wait_for(pending, timeout=5)

                assert live.revision == changed.revision
                assert live.events[0].kind is WatchEventKind.PUT
                assert live.events[0].key_value.key == live_key
                assert live.events[0].key_value.value == b"two"
            finally:
                await watch.aclose()

    run(scenario())


def test_real_etcd_lease_revoke_deletes_attached_key() -> None:
    async def scenario() -> None:
        async with real_backend() as (backend, prefix):
            key = prefix + b"claim"
            lease = await backend.grant_lease(10)
            try:
                attached = await backend.transaction(
                    compares=(),
                    success=(Put(key, b"owner", lease_id=lease.id),),
                )
                current = await backend.range(RangeRequest(key))
                assert current.values[0].lease_id == lease.id
                assert 0 < await backend.keep_alive(lease.id) <= lease.ttl

                watch = backend.watch(
                    start=key,
                    start_revision=attached.revision + 1,
                )
                try:
                    pending = asyncio.create_task(anext(watch))
                    await asyncio.sleep(0)
                    await backend.revoke_lease(lease.id)
                    deleted = await asyncio.wait_for(pending, timeout=5)

                    assert deleted.events[0].kind is WatchEventKind.DELETE
                    assert deleted.events[0].key_value.key == key
                    assert not (await backend.range(RangeRequest(key))).values
                finally:
                    await watch.aclose()
            finally:
                with suppress(Exception):
                    await backend.revoke_lease(lease.id)

    run(scenario())
