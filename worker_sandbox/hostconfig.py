"""The provisioned host configuration, recorded once by setup-host at <control_root>/host.json."""
from __future__ import annotations

from pathlib import Path

from .artifacts import atomic_write, safe_read
from .contracts import ContractError, RuntimeSpec, dumps, loads

NAME = 'host.json'
LIMIT = 4096


def path(control_root: str | None = None) -> Path:
    return Path(control_root if control_root is not None else RuntimeSpec().control_root) / NAME


def read(control_root: str | None = None) -> RuntimeSpec:
    """Load the account, roots and python that setup-host provisioned."""
    target = path(control_root)
    try:
        spec = loads(safe_read(target, target.parent, LIMIT), RuntimeSpec)
    except FileNotFoundError:
        raise ContractError(f'{target} is missing; provision the host with setup-host first') from None
    if Path(spec.control_root) != target.parent:
        raise ContractError(f'{target} describes another control root: {spec.control_root}')
    return spec


def write(spec: RuntimeSpec) -> Path:
    """Publish host.json once; an existing file is never replaced."""
    target = path(spec.control_root)
    atomic_write(target, dumps(spec))
    return target
