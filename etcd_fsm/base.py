from abc import ABC, abstractmethod
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .errors import InvalidFSMKey


@runtime_checkable
class FSMKey(Protocol):
    def fsm_key(self) -> str:
        """Return the normalized relative path identifying this object."""
        ...


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class BaseState[S: Enum](FrozenModel, ABC):
    state: S

    @abstractmethod
    def fsm_key(self) -> str:
        """Return the State Key for this value."""


class Command[R](FrozenModel, ABC):
    @abstractmethod
    def fsm_key(self) -> str:
        """Return the State Key addressed by this command."""


def validate_fsm_key(key: object) -> str:
    if not isinstance(key, str):
        raise InvalidFSMKey(f"FSM key must be str, got {type(key).__name__}")
    if not key:
        raise InvalidFSMKey("FSM key must not be empty")
    if "\x00" in key:
        raise InvalidFSMKey("FSM key must not contain NUL")

    segments = key.split("/")
    if any(not segment for segment in segments):
        raise InvalidFSMKey(f"FSM key must be a normalized relative path: {key!r}")

    invalid_segments = {".", "..", "rpc"}
    invalid = next((segment for segment in segments if segment in invalid_segments), None)
    if invalid is not None:
        raise InvalidFSMKey(f"FSM key contains reserved segment {invalid!r}: {key!r}")

    return key
