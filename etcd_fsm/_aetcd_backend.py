# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from collections.abc import AsyncGenerator, Sequence
from contextlib import suppress
from typing import Any

import aetcd
from aetcd import exceptions as aetcd_exceptions
from aetcd import rpc, rtypes

from ._backend import (
    BackendError,
    CompactedRevision,
    Compare,
    CompareOperator,
    CompareTarget,
    DeleteRange,
    DeleteResult,
    FutureRevision,
    GetRange,
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


class AetcdBackend:
    """Production etcd backend implemented on top of aetcd 1.x."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 2379,
        username: str | None = None,
        password: str | None = None,
        timeout: int | None = None,
        options: dict[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self._client: Any = client or aetcd.Client(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            options=options,
        )

    async def connect(self) -> None:
        try:
            await self._client.connect()
        except Exception as error:
            raise _translate_error(error) from error

    async def range(self, request: RangeRequest) -> RangeResult:
        if request.limit is not None and request.limit <= 0:
            raise ValueError("range limit must be positive")

        await self.connect()
        rpc_request = _range_request(request)
        try:
            response = await self._client.kvstub.Range(
                rpc_request,
                timeout=self._client._timeout,
                metadata=self._client.metadata,
            )
        except Exception as error:
            raise _translate_error(
                error,
                requested_revision=request.revision,
            ) from error

        revision = request.revision or response.header.revision
        values = tuple(_key_value(value) for value in response.kvs)
        if response.more and not values:
            raise BackendError("etcd returned more=true with an empty range page")
        return RangeResult(
            values=values,
            revision=revision,
            more=response.more,
            next_key=values[-1].key + b"\0" if response.more else None,
        )

    async def transaction(
        self,
        *,
        compares: Sequence[Compare],
        success: Sequence[TransactionOperation],
        failure: Sequence[TransactionOperation] = (),
    ) -> TransactionResult:
        success_operations = tuple(success)
        failure_operations = tuple(failure)
        _validate_transaction_operations(success_operations)
        _validate_transaction_operations(failure_operations)

        await self.connect()
        request = rpc.TxnRequest(
            compare=[_compare(compare) for compare in compares],
            success=[_operation(operation) for operation in success_operations],
            failure=[_operation(operation) for operation in failure_operations],
        )
        try:
            response = await self._client.kvstub.Txn(
                request,
                timeout=self._client._timeout,
                metadata=self._client.metadata,
            )
        except Exception as error:
            raise _translate_error(error) from error

        selected = success_operations if response.succeeded else failure_operations
        if len(selected) != len(response.responses):
            raise BackendError("etcd transaction returned an unexpected response count")
        revision = response.header.revision
        responses = tuple(
            _transaction_response(operation, item, revision)
            for operation, item in zip(selected, response.responses, strict=True)
        )
        return TransactionResult(
            succeeded=response.succeeded,
            responses=responses,
            revision=revision,
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

        await self.connect()
        try:
            lease = await self._client.lease(ttl)
        except Exception as error:
            raise _translate_error(error) from error
        return Lease(id=lease.id, ttl=lease.ttl)

    async def keep_alive(self, lease_id: LeaseID) -> int:
        await self.connect()
        try:
            response = await self._client.refresh_lease(lease_id)
        except Exception as error:
            raise _translate_error(error, lease_id=lease_id) from error
        if response is None or response.TTL <= 0:
            raise LeaseNotFound(lease_id)
        return response.TTL

    async def revoke_lease(self, lease_id: LeaseID) -> None:
        await self.connect()
        try:
            await self._client.revoke_lease(lease_id)
        except Exception as error:
            raise _translate_error(error, lease_id=lease_id) from error

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception as error:
            raise _translate_error(error) from error

    async def _watch(
        self,
        *,
        start: bytes,
        end: bytes | None,
        start_revision: Revision,
    ) -> AsyncGenerator[WatchBatch]:
        await self.connect()
        watch: Any = None
        try:
            watch = await self._client.watch(
                start,
                range_end=end,
                start_revision=start_revision,
            )
            async for event in watch:
                key_value = _key_value(event.kv)
                kind = (
                    WatchEventKind.PUT
                    if event.kind == rtypes.EventKind.PUT
                    else WatchEventKind.DELETE
                )
                yield WatchBatch(
                    events=(WatchEvent(kind=kind, key_value=key_value),),
                    revision=key_value.mod_revision,
                )
        except Exception as error:
            raise _translate_error(
                error,
                requested_revision=start_revision,
            ) from error
        finally:
            if watch is not None:
                with suppress(Exception):
                    await watch.cancel()


def _range_request(request: RangeRequest) -> Any:
    return rpc.RangeRequest(
        key=request.start,
        range_end=request.end or b"",
        limit=request.limit or 0,
        revision=request.revision or 0,
        sort_order=rpc.RangeRequest.ASCEND,
        sort_target=rpc.RangeRequest.KEY,
        serializable=False,
    )


def _compare(compare: Compare) -> Any:
    result = {
        CompareOperator.EQUAL: rpc.Compare.EQUAL,
        CompareOperator.NOT_EQUAL: rpc.Compare.NOT_EQUAL,
        CompareOperator.GREATER: rpc.Compare.GREATER,
        CompareOperator.LESS: rpc.Compare.LESS,
    }[compare.operator]
    target = {
        CompareTarget.VALUE: rpc.Compare.VALUE,
        CompareTarget.CREATE_REVISION: rpc.Compare.CREATE,
        CompareTarget.MOD_REVISION: rpc.Compare.MOD,
        CompareTarget.VERSION: rpc.Compare.VERSION,
        CompareTarget.LEASE: rpc.Compare.LEASE,
    }[compare.target]
    message = rpc.Compare(
        key=compare.key,
        result=result,
        target=target,
    )

    if compare.target is CompareTarget.VALUE:
        if not isinstance(compare.operand, bytes):
            raise BackendError("VALUE compare requires a bytes operand")
        message.value = compare.operand
    else:
        if not isinstance(compare.operand, int):
            raise BackendError(f"{compare.target} compare requires an int operand")
        field = {
            CompareTarget.CREATE_REVISION: "create_revision",
            CompareTarget.MOD_REVISION: "mod_revision",
            CompareTarget.VERSION: "version",
            CompareTarget.LEASE: "lease",
        }[compare.target]
        setattr(message, field, compare.operand)
    return message


def _operation(operation: TransactionOperation) -> Any:
    if isinstance(operation, Put):
        return rpc.RequestOp(
            request_put=rpc.PutRequest(
                key=operation.key,
                value=operation.value,
                lease=operation.lease_id or 0,
            )
        )
    if isinstance(operation, DeleteRange):
        return rpc.RequestOp(
            request_delete_range=rpc.DeleteRangeRequest(
                key=operation.start,
                range_end=operation.end or b"",
            )
        )
    return rpc.RequestOp(request_range=_range_request(operation.request))


def _validate_transaction_operations(
    operations: tuple[TransactionOperation, ...],
) -> None:
    for operation in operations:
        if isinstance(operation, GetRange) and operation.request.revision is not None:
            raise BackendError("transactional GetRange cannot request a historical revision")


def _transaction_response(
    operation: TransactionOperation,
    response: Any,
    revision: Revision,
) -> TransactionResponse:
    response_type = response.WhichOneof("response")
    if isinstance(operation, Put) and response_type == "response_put":
        return PutResult(key=operation.key, revision=revision)
    if isinstance(operation, DeleteRange) and response_type == "response_delete_range":
        return DeleteResult(deleted=response.response_delete_range.deleted)
    if isinstance(operation, GetRange) and response_type == "response_range":
        item = response.response_range
        values = tuple(_key_value(value) for value in item.kvs)
        if item.more and not values:
            raise BackendError("etcd returned more=true with an empty transaction range")
        return RangeResult(
            values=values,
            revision=revision,
            more=item.more,
            next_key=values[-1].key + b"\0" if item.more else None,
        )
    raise BackendError(
        f"etcd transaction response {response_type!r} does not match {type(operation).__name__}"
    )


def _key_value(value: Any) -> KeyValue:
    return KeyValue(
        key=value.key,
        value=value.value,
        create_revision=value.create_revision,
        mod_revision=value.mod_revision,
        version=value.version,
        lease_id=value.lease or None,
    )


def _translate_error(
    error: Exception,
    *,
    requested_revision: Revision | None = None,
    lease_id: LeaseID | None = None,
) -> BackendError:
    if isinstance(error, BackendError):
        return error
    if isinstance(error, aetcd_exceptions.RevisionCompactedError):
        return CompactedRevision(
            requested=requested_revision or error.compacted_revision,
            compacted=error.compacted_revision,
        )

    details_method = getattr(error, "details", None)
    details = str(details_method()) if callable(details_method) else str(error)
    normalized = details.lower()
    if "compacted" in normalized and requested_revision is not None:
        return CompactedRevision(
            requested=requested_revision,
            compacted=requested_revision,
        )
    if "future revision" in normalized and requested_revision is not None:
        return FutureRevision(requested=requested_revision, current=0)
    if "lease not found" in normalized and lease_id is not None:
        return LeaseNotFound(lease_id)
    return BackendError(details)
