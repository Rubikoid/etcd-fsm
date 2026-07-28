class FSMError(Exception):
    """Base class for errors raised by etcd-fsm."""


class DefinitionError(FSMError):
    """Base class for invalid declarations detected before runtime starts."""


class SchemaDefinitionError(DefinitionError):
    """The shared FSM schema is incomplete or internally inconsistent."""


class ProcessorDefinitionError(DefinitionError):
    """A processor function has an unsupported or conflicting signature."""


class InvalidFSMKey(FSMError, ValueError):
    """A model produced a key that cannot be used in the managed namespace."""
