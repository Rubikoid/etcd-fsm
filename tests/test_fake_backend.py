import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from etcd_fsm._backend import (
    BackendClosed,
    CompactedRevision,
    Compare,
    CompareOperator,
    CompareTarget,
    DeleteRange,
    EtcdBackend,
    GetRange,
    Put,
    RangeRequest,
    WatchEventKind,
    prefix_range,
    prefix_range_end,
)
from etcd_fsm._fake_backend import FakeEtcdBackend


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def test_fake_backend_satisfies_backend_protocol_statically() -> None:
    backend: EtcdBackend = FakeEtcdBackend()
    run(backend.connect())
    run(backend.close())


def test_compare_and_put_is_atomic() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        compare_missing = Compare(
            key=b"/state/orders/one",
            target=CompareTarget.CREATE_REVISION,
            operator=CompareOperator.EQUAL,
            operand=0,
        )

        created = await backend.transaction(
            compares=(compare_missing,),
            success=(Put(b"/state/orders/one", b"created"),),
        )
        busy = await backend.transaction(
            compares=(compare_missing,),
            success=(Put(b"/state/orders/one", b"overwritten"),),
            failure=(GetRange(RangeRequest(b"/state/orders/one")),),
        )

        assert created.succeeded
        assert created.revision == 1
        assert not busy.succeeded
        assert busy.revision == 1
        current = await backend.range(RangeRequest(b"/state/orders/one"))
        assert current.values[0].value == b"created"

    run(scenario())


def test_range_pagination_remains_pinned_to_first_revision() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        prefix = b"/state/orders/"
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
        assert first.revision == 1
        assert first.more
        assert first.next_key is not None

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


def test_watch_replays_history_then_streams_live_batches() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        prefix = b"/state/orders/"
        await backend.transaction(
            compares=(),
            success=(Put(prefix + b"one", b"one"),),
        )

        watch = backend.watch(
            start=prefix,
            end=prefix_range_end(prefix),
            start_revision=1,
        )
        historical = await anext(watch)
        assert historical.revision == 1
        assert historical.events[0].key_value.key == prefix + b"one"

        pending = asyncio.ensure_future(anext(watch))
        await asyncio.sleep(0)
        await backend.transaction(
            compares=(),
            success=(Put(prefix + b"two", b"two"),),
        )
        live = await pending
        assert live.revision == 2
        assert live.events[0].key_value.key == prefix + b"two"
        await watch.aclose()

    run(scenario())


def test_compaction_rejects_old_ranges_and_watches() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        await backend.transaction(
            compares=(),
            success=(Put(b"/state/orders/one", b"one"),),
        )
        await backend.compact(1)

        with pytest.raises(CompactedRevision):
            await backend.range(RangeRequest(b"/state/orders/", end=b"/state/orders0", revision=1))

        watch = backend.watch(
            start=b"/state/orders/",
            end=b"/state/orders0",
            start_revision=1,
        )
        with pytest.raises(CompactedRevision):
            await anext(watch)

        current = await backend.range(RangeRequest(b"/state/orders/one"))
        assert current.values[0].value == b"one"

    run(scenario())


def test_lease_expiration_deletes_attached_keys_in_one_revision() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        lease = await backend.grant_lease(5)
        await backend.transaction(
            compares=(),
            success=(Put(b"/claims/one", b"owner", lease.id),),
        )

        watch = backend.watch(
            start=b"/claims/",
            end=prefix_range_end(b"/claims/"),
            start_revision=2,
        )
        pending = asyncio.ensure_future(anext(watch))
        await asyncio.sleep(0)
        await backend.advance_time(5)

        expired = await pending
        assert expired.revision == 2
        assert expired.events[0].kind is WatchEventKind.DELETE
        assert expired.events[0].key_value.value == b""
        assert expired.events[0].key_value.lease_id is None
        assert not (await backend.range(RangeRequest(b"/claims/one"))).values
        await watch.aclose()

    run(scenario())


def test_keep_alive_extends_deterministic_lease_deadline() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        lease = await backend.grant_lease(5)

        await backend.advance_time(4)
        assert await backend.keep_alive(lease.id) == 5
        await backend.advance_time(4)
        assert await backend.lease_time_to_live(lease.id) == 1

    run(scenario())


def test_transaction_watch_events_share_one_revision() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        watch = backend.watch(
            start=b"/state/",
            end=prefix_range_end(b"/state/"),
            start_revision=1,
        )
        pending = asyncio.ensure_future(anext(watch))
        await asyncio.sleep(0)

        result = await backend.transaction(
            compares=(),
            success=(
                Put(b"/state/one", b"one"),
                Put(b"/state/two", b"two"),
            ),
        )
        batch = await pending

        assert result.revision == 1
        assert batch.revision == 1
        assert len(batch.events) == 2
        assert {event.key_value.mod_revision for event in batch.events} == {1}
        await watch.aclose()

    run(scenario())


def test_prefix_range_end_handles_normal_and_max_prefixes() -> None:
    assert prefix_range_end(b"/state/orders/") == b"/state/orders0"
    assert prefix_range_end(b"\xff") == b"\0"


def test_closed_backend_rejects_new_operations() -> None:
    async def scenario() -> None:
        backend = FakeEtcdBackend()
        await backend.close()

        with pytest.raises(BackendClosed):
            await backend.range(RangeRequest(b"/state/one"))

    run(scenario())
