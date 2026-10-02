"""Descriptor-based streaming artifact reads and atomic publication."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterator

from .contracts import ArtifactRef, Snapshot, digest, make
from .worker_files import directory_fd, file_chunks, file_info, open_file, regular_files


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Open every ancestor without following links, including the root itself."""
    fd = directory_fd(Path(os.path.abspath(path)))
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def safe_open(path: Path, root: Path):
    """Open a regular file beneath root without following any link."""
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    relative = path.relative_to(root).as_posix()
    with os.fdopen(open_file(root, relative, os.O_RDONLY), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('artifact is not a regular file')
        yield stream


def safe_read(path: Path, root: Path, limit: int | None = None) -> bytes:
    """Decode inputs in memory; explicit bounds apply only to control documents."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError('byte limit must be a nonnegative integer')
    with safe_open(path, root) as stream:
        return b''.join(file_chunks(stream.fileno(), limit))


def reference_file(path: Path, root: Path) -> ArtifactRef:
    with safe_open(path, root) as stream:
        return make(ArtifactRef, path=path.relative_to(root).as_posix(), **file_info(stream.fileno()))


def _sync_directory(path: Path) -> None:
    with _directory(path) as fd:
        os.fsync(fd)


@contextmanager
def atomic_output(path: Path):
    """Publish only after the producer succeeds; never replace an artifact."""
    path = Path(path)
    with _directory(path.parent):
        pass
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        _sync_directory(path.parent)
    finally:
        os.unlink(temporary)


def _manifest(root: Path, *, destination: Path | None = None, exclude_generated: bool = True) -> dict:
    files: dict[str, dict] = {}
    with _directory(root) as fd:
        for rel, child in regular_files(fd, exclude=exclude_generated):
            if destination is None:
                files[rel] = file_info(child)
            else:
                target = destination / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(child, dict):
                    os.symlink(child['target'], target)
                    files[rel] = child
                    continue
                with target.open('xb') as handle:
                    files[rel] = file_info(child, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                target.chmod(0o500 if files[rel]['executable'] else 0o400)
    return files


def _rename_directory(source: Path, destination: Path, *, exchange=False) -> None:
    """Linux atomically publishes a new directory or exchanges complete trees."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOTSUP, 'atomic directory rename is unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 2 if exchange else 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def replace_directory(source: Path, destination: Path) -> None:
    """Keep the existing mirror in place until its replacement is complete.

    After an exchange, the caller's staging path contains the previous tree.
    There is no interval in which interruption leaves the destination missing.
    """
    with _directory(source):
        pass
    try:
        with _directory(destination):
            pass
    except FileNotFoundError:
        _rename_directory(source, destination)
    else:
        _rename_directory(source, destination, exchange=True)


def _snapshot(source: Path, destination: Path, *, exclude_generated: bool = True) -> dict:
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source == destination or source in destination.parents:
        raise ValueError('snapshot destination must be outside the source tree')
    if os.path.lexists(destination):
        raise FileExistsError(str(destination))
    with _directory(destination.parent):
        pass
    temporary = Path(tempfile.mkdtemp(prefix='.' + destination.name + '.', dir=destination.parent))
    try:
        kwargs = dict(exclude_generated=exclude_generated)
        before = _manifest(source, **kwargs)
        copied = _manifest(source, destination=temporary, **kwargs)
        after = _manifest(source, **kwargs)
        if before != copied or before != after:
            raise ValueError('source changed while snapshotting')
        _references(copied)
        for directory, _, _ in os.walk(temporary, topdown=False):
            _sync_directory(Path(directory))
        _rename_directory(temporary, destination)
        _sync_directory(destination.parent)
        return copied
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def atomic_write(path: Path, data: bytes) -> ArtifactRef:
    """Publish a new artifact and return its basename-relative identity."""
    if not isinstance(data, bytes):
        raise ValueError('artifact must be bytes')
    reference = make(ArtifactRef, path=Path(path).name, digest=digest(data), size=len(data))
    with atomic_output(Path(path)) as stream:
        stream.write(data)
    return reference


def copy_artifact(source: Path, destination: Path, root: Path, expected: ArtifactRef) -> ArtifactRef:
    with atomic_output(destination) as output, safe_open(source, root) as stream:
        info = file_info(stream.fileno(), output)
        result = make(ArtifactRef, path=destination.name, **info)
        if (result.size, result.digest) != (expected.size, expected.digest):
            raise ValueError('evidence changed while being copied')
    return result


def _references(manifest: dict) -> tuple[ArtifactRef, ...]:
    return tuple(make(ArtifactRef, path=path, digest=info['digest'],
                      size=info['size'], source='candidate', executable=info['executable'],
                      kind=info.get('kind', 'file'), target=info.get('target'))
                 for path, info in sorted(manifest.items()))


def tree_manifest(root: Path, *, exclude_generated: bool = True) -> tuple[ArtifactRef, ...]:
    return _references(_manifest(Path(root), exclude_generated=exclude_generated))


def snapshot(source: Path, destination: Path, *, run_id: str,
             exclude_generated: bool = True) -> Snapshot:
    make(Snapshot, run_id=run_id, digest=digest(()), files=())
    files = _references(_snapshot(Path(source), Path(destination), exclude_generated=exclude_generated))
    return make(Snapshot, run_id=run_id, digest=digest(files), files=files)
