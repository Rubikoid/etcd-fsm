from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal


@dataclass(frozen=True, slots=True)
class Retry:
    after: timedelta | None = None
    kind: Literal["retry"] = field(default="retry", init=False)

    def __post_init__(self) -> None:
        if self.after is not None and self.after < timedelta(0):
            raise ValueError("Retry delay must not be negative")


@dataclass(frozen=True, slots=True)
class Applied[S]:
    state: S
    kind: Literal["applied"] = field(default="applied", init=False)


@dataclass(frozen=True, slots=True)
class Rejected[E]:
    error: E
    kind: Literal["rejected"] = field(default="rejected", init=False)


@dataclass(frozen=True, slots=True)
class Ignored:
    kind: Literal["ignored"] = field(default="ignored", init=False)


@dataclass(frozen=True, slots=True)
class Delete:
    kind: Literal["delete"] = field(default="delete", init=False)
