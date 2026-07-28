from types import UnionType
from typing import Annotated, Any, TypeAliasType, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel


def strip_annotated(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def flatten_union(annotation: Any) -> tuple[Any, ...]:
    annotation = strip_annotated(annotation)
    if isinstance(annotation, TypeAliasType):
        return flatten_union(annotation.__value__)

    if get_origin(annotation) in (Union, UnionType):
        flattened: list[Any] = []
        for member in get_args(annotation):
            flattened.extend(flatten_union(member))
        return tuple(dict.fromkeys(flattened))

    return (annotation,)


def pydantic_generic_argument(model: type[BaseModel], origin: type[BaseModel]) -> Any:
    result = _find_pydantic_generic_argument(model, origin, {})
    if result is _MISSING:
        raise LookupError(f"{model!r} is not a specialization of {origin!r}")
    return result


def pydantic_generic_origin(model: type[BaseModel]) -> type[BaseModel]:
    metadata = getattr(model, "__pydantic_generic_metadata__", None)
    if metadata and metadata["origin"] is not None:
        return metadata["origin"]
    return model


_MISSING = object()


def _find_pydantic_generic_argument(
    model: type[BaseModel],
    target: type[BaseModel],
    bindings: dict[TypeVar, Any],
) -> Any:
    metadata = getattr(model, "__pydantic_generic_metadata__", None)
    concrete = model
    current_bindings = dict(bindings)

    if metadata and metadata["origin"] is not None:
        concrete = metadata["origin"]
        parameters = getattr(concrete, "__pydantic_generic_metadata__", {})["parameters"]
        arguments = metadata["args"]
        current_bindings.update(
            {
                parameter: _substitute_typevars(argument, bindings)
                for parameter, argument in zip(parameters, arguments, strict=True)
            }
        )

    if concrete is target:
        parameters = getattr(target, "__pydantic_generic_metadata__", {})["parameters"]
        if len(parameters) != 1:
            raise TypeError(f"{target!r} must have exactly one generic parameter")
        return _substitute_typevars(parameters[0], current_bindings)

    for base in concrete.__bases__:
        if not isinstance(base, type) or not issubclass(base, BaseModel):
            continue
        result = _find_pydantic_generic_argument(base, target, current_bindings)
        if result is not _MISSING:
            return result

    return _MISSING


def _substitute_typevars(annotation: Any, bindings: dict[TypeVar, Any]) -> Any:
    while isinstance(annotation, TypeVar) and annotation in bindings:
        replacement = bindings[annotation]
        if replacement is annotation:
            break
        annotation = replacement
    return annotation
