"""Typed wire contracts for the worker benchmark controller."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal, TypeVar

import msgspec

Text = Annotated[str, msgspec.Meta(min_length=1)]
Digest = Annotated[str, msgspec.Meta(pattern=r"^sha256:[0-9a-f]{64}$")]
Nonnegative = Annotated[int, msgspec.Meta(ge=0)]
# One explicit native setting value; no placeholder may stand in for a frozen choice.
Setting = Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Outcome = Literal["completed", "incomplete", "cancelled", "provider_error", "protocol_error", "harness_error", "interrupted"]
Cleanup = Literal["pending", "confirmed", "failed"]


class ContractError(ValueError):
    """An input or persisted contract is invalid."""


class Halt(Exception):
    """A known run outcome propagated through nested native invocations."""

    def __init__(self, outcome: Outcome, detail: str):
        super().__init__(detail)
        self.outcome = outcome


class Model(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    pass


class ArtifactRef(Model):
    path: Text
    digest: Digest
    size: Nonnegative
    source: Text = "controller"
    executable: bool = False
    kind: Literal['file', 'symlink'] = 'file'
    target: str | None = None

    def __post_init__(self):
        if self.path.startswith("/") or "\\" in self.path or any(x in {"", ".", ".."} for x in self.path.split("/")):
            raise ValueError("artifact path must be a normalized relative path")
        if (self.kind == 'symlink') != (self.target is not None):
            raise ValueError('Only symlink references carry link metadata')


class RuntimeSpec(Model):
    account: Literal["food-delivery"] = "food-delivery"
    worker_root: Literal["/var/lib/benchkit-worker"] = "/var/lib/benchkit-worker"
    control_root: Literal["/var/lib/benchkit-controller"] = "/var/lib/benchkit-controller"
    python: Literal["/usr/bin/python3"] = "/usr/bin/python3"
    workspace_storage: Literal["host_directory_no_hard_quota"] = "host_directory_no_hard_quota"


class Interval(Model):
    kind: Literal['native', 'setup']
    clock_epoch: Text
    start: float
    end: float
    call_id: Text | None = None

    def __post_init__(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.end < self.start:
            raise ValueError('Invalid monotonic interval')


class RuntimeRequest(Model):
    spec: RuntimeSpec
    name: Text
    workspace: Text
    home: Text
    argv: tuple[str, ...]
    log_dir: Text
    stdin: bytes | None = None


class RuntimeResult(Model):
    outcome: Outcome
    exit_code: int | None
    wall_sec: Annotated[float, msgspec.Meta(ge=0)]
    stdout_path: Text | None
    stderr_path: Text | None
    cleanup: Cleanup
    cleanup_error: str | None = None
    error: str | None = None
    interval: Interval | None = None


T = TypeVar("T")


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("nonfinite number")
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ContractError("JSON object keys must be strings")
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_value(raw: bytes | str):
    """Strict JSON syntax, duplicate keys and finite values for every boundary."""
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda text: (_ for _ in ()).throw(ContractError(f"invalid JSON number: {text}")))
        _finite(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError(str(exc)) from exc


def validate(value, cls: type[T]) -> T:
    try:
        data = msgspec.to_builtins(value)
        _finite(data)
        return msgspec.convert(data, type=cls, strict=True)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContractError(str(exc)) from exc


def loads(raw: bytes | str, cls: type[T]) -> T:
    return validate(json_value(raw), cls)


def make(cls: type[T], **fields) -> T:
    return validate(fields, cls)


def dumps(value) -> bytes:
    normalized = validate(value, type(value)) if isinstance(value, Model) else value
    data = msgspec.to_builtins(normalized)
    _finite(data)
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else dumps(value)).hexdigest()

