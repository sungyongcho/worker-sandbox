"""Nonblocking filesystem ownership, independent of records and execution."""
from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
from pathlib import Path

from .contracts import ContractError


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Nonblocking operation ownership; hold outside short database transactions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError(f"another operation owns {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
