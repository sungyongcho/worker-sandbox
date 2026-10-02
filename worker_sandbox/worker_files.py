"""Standard-library file bridge, executed only as the unprivileged worker user."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
from io import BytesIO

RUN_DIRECTORIES = ('workspace', 'home', 'tmp')
EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}
CHUNK_BYTES = 65536


def directory_fd(path: Path, *, create=False) -> int:
    if not path.is_absolute():
        raise ValueError('absolute directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _relative_parts(relative: str) -> list[str]:
    if not isinstance(relative, str) or '\\' in relative or '\0' in relative:
        raise ValueError('invalid relative path')
    parts = relative.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ValueError('invalid relative path')
    return parts


def _parent_fd(root: Path, relative: str, *, create_parents=False) -> tuple[int, str]:
    parts = _relative_parts(relative)
    directory = directory_fd(root)
    try:
        for part in parts[:-1]:
            if create_parents:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory, parts[-1]
    except BaseException:
        os.close(directory)
        raise


def open_file(root: Path, relative: str, flags: int, *, create_parents=False) -> int:
    directory, name = _parent_fd(root, relative, create_parents=create_parents)
    try:
        return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    finally:
        os.close(directory)


def file_chunks(fd: int, limit: int | None = None):
    """Read the observed regular file size; fail if its identity changes."""
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('regular files required')
    if limit is not None and before.st_size > limit:
        raise ValueError('control document exceeds byte limit')
    remaining = before.st_size
    while remaining:
        chunk = os.read(fd, min(CHUNK_BYTES, remaining))
        if not chunk:
            raise ValueError('file changed while being read')
        remaining -= len(chunk)
        yield chunk
    after = os.fstat(fd)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('file changed while being read')


def read_file(root: Path, relative: str, limit: int | None = None) -> bytes:
    with os.fdopen(open_file(root, relative, os.O_RDONLY), 'rb') as stream:
        return b''.join(file_chunks(stream.fileno(), limit))


def file_info(fd: int, destination=None) -> dict:
    if isinstance(fd, dict):
        return fd
    identity, size = hashlib.sha256(), 0
    executable = bool(os.fstat(fd).st_mode & 0o111)
    for chunk in file_chunks(fd):
        identity.update(chunk)
        size += len(chunk)
        if destination is not None:
            destination.write(chunk)
    return {'digest': 'sha256:' + identity.hexdigest(), 'size': size, 'executable': executable}


def regular_files(fd: int, *, exclude: bool, prefix: str = ''):
    """Traverse descriptors without following links or suppressing directory errors."""
    with os.scandir(fd) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        relative = prefix + name
        mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            if name == '.git' or exclude and name in EXCLUDED:
                continue
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                yield from regular_files(child, exclude=exclude, prefix=relative + '/')
            finally:
                os.close(child)
        elif stat.S_ISREG(mode):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                yield relative, child
            finally:
                os.close(child)
        elif stat.S_ISLNK(mode):
            target = os.readlink(name, dir_fd=fd)
            raw = target.encode()
            yield relative, {'kind': 'symlink', 'target': target, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                             'size': len(raw), 'executable': False}
        elif not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            raise ValueError(f'unsupported source entry: {relative}')
        # IPC is represented in observe_tree, and is not a product file.


def observe_tree(root: Path) -> dict:
    """Factual checkpoint, including inconsistent reads and non-product IPC."""
    rows = []
    def visit(fd, prefix=''):
        try:
            names = sorted(os.listdir(fd))
        except OSError as exc:
            rows.append({'path': prefix, 'status': 'unavailable', 'reason': f'{type(exc).__name__}: {exc}'})
            return
        for name in names:
            relative = prefix + name
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if name in EXCLUDED:
                        continue
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        visit(child, relative + '/')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(child, 'rb') as stream:
                        raw = b''.join(file_chunks(stream.fileno()))
                    row = {'path': relative, 'kind': 'file', 'status': 'observed', 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                           'size': len(raw), 'executable': bool(info.st_mode & 0o111)}
                    try:
                        row['text'] = raw.decode('utf-8')
                    except UnicodeError:
                        row.update(status='unsupported', reason='Non-text source; complete original encoded locally',
                                   base64=base64.b64encode(raw).decode())
                    rows.append(row)
                elif stat.S_ISLNK(info.st_mode):
                    rows.append({'path': relative, 'kind': 'symlink', 'target': os.readlink(name, dir_fd=fd), 'status': 'observed'})
                else:
                    rows.append({'path': relative, 'kind': 'runtime', 'mode': info.st_mode, 'status': 'unsupported',
                                 'reason': 'Non-product runtime entry; not opened or exported as source'})
            except (OSError, ValueError) as exc:
                rows.append({'path': relative, 'status': 'inconsistent' if isinstance(exc, ValueError) else 'unavailable',
                             'reason': f'{type(exc).__name__}: {exc}'})
    fd = directory_fd(root)
    try:
        visit(fd)
    finally:
        os.close(fd)
    return {'entries': rows, 'observation_complete': all(r['status'] == 'observed' or r.get('kind') == 'runtime' for r in rows)}


def file_list(root: Path, exclude: bool) -> list[dict]:
    fd = directory_fd(root)
    try:
        return sorted(({'path': path, **file_info(child)}
                       for path, child in regular_files(fd, exclude=exclude)), key=lambda row: row['path'])
    finally:
        os.close(fd)


def _archive_refs(refs: list[dict]) -> list[dict]:
    """Validate the manifest before creating or reading any filesystem entries."""
    entries = {}
    for ref in refs:
        path = ref['path']
        _relative_parts(path)
        kind, target = ref.get('kind', 'file'), ref.get('target')
        if kind not in {'file', 'symlink'} or type(ref['size']) is not int or ref['size'] < 0:
            raise ValueError('invalid archive entry')
        if (type(ref['executable']) is not bool or not isinstance(ref['digest'], str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', ref['digest'])):
            raise ValueError('invalid archive identity')
        if kind == 'symlink':
            if not isinstance(target, str) or not target or '\0' in target or ref['executable']:
                raise ValueError('invalid archive symlink')
            raw = target.encode()
            if len(raw) != ref['size'] or 'sha256:' + hashlib.sha256(raw).hexdigest() != ref['digest']:
                raise ValueError('archive symlink identity mismatch')
        elif target is not None:
            raise ValueError('regular archive entry has a link target')
        if path in entries:
            raise ValueError('duplicate archive path')
        entries[path] = {key: ref[key] for key in ('path', 'digest', 'size', 'executable')}
        entries[path].update(kind=kind, target=target)
    for path in entries:
        parts = path.split('/')
        if any('/'.join(parts[:index]) in entries for index in range(1, len(parts))):
            raise ValueError('archive entry has a non-directory parent')
    return [entries[path] for path in sorted(entries)]


def file_refs(root: Path, paths: list[str]) -> list[dict]:
    """Fingerprint an explicit evidence selection without following links."""
    refs = []
    for path in paths:
        parent, name = _parent_fd(root, path)
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(name, dir_fd=parent)
                raw = target.encode()
                info = {'kind': 'symlink', 'target': target, 'size': len(raw),
                        'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'executable': False}
                after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_mtime_ns', 'st_ctime_ns')
                if any(getattr(before, key) != getattr(after, key) for key in fields):
                    raise ValueError('symlink changed while being read')
            else:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as stream:
                    info = file_info(stream.fileno())
            refs.append({'path': path, **info})
        finally:
            os.close(parent)
    return _archive_refs(refs)


def _archive_header(ref: dict) -> bytes:
    member = tarfile.TarInfo(ref['path'])
    member.mode = 0o700 if ref['executable'] else 0o600
    if ref['kind'] == 'symlink':
        member.type, member.linkname = tarfile.SYMTYPE, ref['target']
    else:
        member.size = ref['size']
    return member.tobuf(format=tarfile.PAX_FORMAT, encoding='utf-8', errors='strict')


def _write_all(output, raw: bytes) -> None:
    remaining = memoryview(raw)
    while remaining:
        written = output.write(remaining)
        if written is None or written <= 0:
            raise OSError('archive output stopped accepting bytes')
        remaining = remaining[written:]


def _read_exact(source, size: int) -> bytes:
    blocks = []
    while size:
        block = source.read(min(size, CHUNK_BYTES))
        if not block:
            raise ValueError('truncated archive')
        blocks.append(block)
        size -= len(block)
    return b''.join(blocks)


def _archive_padding(size: int) -> int:
    return -size % tarfile.BLOCKSIZE


def _archive_trailer(size: int) -> bytes:
    end = 2 * tarfile.BLOCKSIZE
    return bytes(end + (-(size + end) % tarfile.RECORDSIZE))


def write_archive(root: Path, refs: list[dict], output) -> None:
    """Stream a deterministic POSIX/PAX tar, verifying the selected source bytes."""
    entries = _archive_refs(refs)
    os.close(directory_fd(root))
    archive_size = 0
    for ref in entries:
        header = _archive_header(ref)
        _write_all(output, header)
        archive_size += len(header)
        if ref['kind'] == 'symlink':
            if file_refs(root, [ref['path']]) != [ref]:
                raise ValueError('archive source symlink changed')
            continue
        with os.fdopen(open_file(root, ref['path'], os.O_RDONLY), 'rb') as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != ref['size'] or bool(before.st_mode & 0o111) != ref['executable']:
                raise ValueError('archive source identity changed')
            identity = hashlib.sha256()
            for chunk in file_chunks(stream.fileno()):
                identity.update(chunk)
                _write_all(output, chunk)
            if 'sha256:' + identity.hexdigest() != ref['digest']:
                raise ValueError('archive source digest changed')
        padding = _archive_padding(ref['size'])
        _write_all(output, bytes(padding))
        archive_size += ref['size'] + padding
    _write_all(output, _archive_trailer(archive_size))


def _receive_file(source, destination: Path, ref: dict) -> None:
    fd = open_file(destination, ref['path'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_parents=True)
    with os.fdopen(fd, 'wb') as stream:
        remaining, identity = ref['size'], hashlib.sha256()
        while remaining:
            chunk = _read_exact(source, min(remaining, CHUNK_BYTES))
            stream.write(chunk)
            identity.update(chunk)
            remaining -= len(chunk)
        if 'sha256:' + identity.hexdigest() != ref['digest']:
            raise ValueError('archive payload digest mismatch')
        os.fchmod(fd, 0o700 if ref['executable'] else 0o600)
        stream.flush()
        os.fsync(fd)


def read_archive(source, destination: Path, refs: list[dict]) -> None:
    """Receive into a new staging tree; callers scan secrets before publishing it.

    Only this manifest's canonical tar headers and framing are accepted. This
    rejects links used as directories, extra members and truncated trailers
    without interpreting untrusted extraction instructions.
    """
    entries = _archive_refs(refs)
    parent = directory_fd(destination.parent)
    try:
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
        try:
            archive_size = 0
            for ref in entries:
                header = _archive_header(ref)
                if _read_exact(source, len(header)) != header:
                    raise ValueError('archive header does not match manifest')
                archive_size += len(header)
                if ref['kind'] == 'symlink':
                    fd, name = _parent_fd(destination, ref['path'], create_parents=True)
                    try:
                        os.symlink(ref['target'], name, dir_fd=fd)
                    finally:
                        os.close(fd)
                else:
                    _receive_file(source, destination, ref)
                    padding = _archive_padding(ref['size'])
                    if _read_exact(source, padding) != bytes(padding):
                        raise ValueError('invalid archive payload padding')
                    archive_size += ref['size'] + padding
            trailer = _archive_trailer(archive_size)
            if _read_exact(source, len(trailer)) != trailer or source.read(1):
                raise ValueError('invalid archive trailer')
            for folder, _, _ in os.walk(destination, topdown=False, followlinks=False):
                fd = directory_fd(Path(folder))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            shutil.rmtree(destination.name, dir_fd=parent)
            raise
    finally:
        os.close(parent)


def push_tree(root: Path, relative: str, refs: list[dict], source) -> None:
    """Publish a verified transfer only over the bridge's empty destination."""
    parts = _relative_parts(relative)
    destination = root.joinpath(*parts)
    parent = directory_fd(destination.parent)
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            original = os.fstat(fd)
            if os.listdir(fd):
                raise ValueError('archive destination must be empty')
        finally:
            os.close(fd)
        with tempfile.TemporaryDirectory(prefix='.transfer-', dir=destination.parent) as staging:
            tree = Path(staging) / 'tree'
            read_archive(source, tree, refs)
            current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                raise ValueError('archive destination changed during transfer')
            os.rename(tree, parts[-1], dst_dir_fd=parent)
            os.fsync(parent)
    finally:
        os.close(parent)


def put_file(root: Path, relative: str, data: bytes, executable: bool) -> None:
    put_stream(root, relative, BytesIO(data), executable)


def put_stream(root: Path, relative: str, source, executable: bool) -> None:
    parts = relative.split('/')
    temporary = '/'.join([*parts[:-1], '.transfer-' + os.urandom(16).hex()])
    fd = open_file(root, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_parents=True)
    parent = directory_fd(root.joinpath(*parts[:-1]))
    try:
        with os.fdopen(fd, 'wb') as stream:
            while chunk := source.read(CHUNK_BYTES):
                stream.write(chunk)
            os.fchmod(stream.fileno(), 0o700 if executable else 0o600)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary.split('/')[-1], parts[-1], src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.fsync(parent)
    finally:
        os.unlink(temporary.split('/')[-1], dir_fd=parent)
        os.close(parent)


def dispatch(root: Path, request: dict):
    action = request['action']
    if action == 'empty':
        return not any(root.parent.iterdir())
    if action == 'init':
        if any(root.parent.iterdir()):
            raise ValueError('worker home is not empty; recover or inspect remaining state')
        root.mkdir(mode=0o700)
        for relative in RUN_DIRECTORIES:
            (root / relative).mkdir(mode=0o700)
        return True
    if action == 'remove' and not root.exists() and not root.is_symlink():
        return True
    if root.is_symlink() or not root.is_dir():
        raise ValueError('missing or unsafe worker run')
    if action == 'check':
        if {path.name for path in root.parent.iterdir()} != {root.name}:
            raise ValueError('another run or unexpected worker state is present')
        for relative in ('', *RUN_DIRECTORIES):
            info = (root / relative).lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or (not relative and info.st_mode & 0o077)):
                raise ValueError('worker run requires private owned directories')
        return True
    if action == 'put':
        put_file(root, request['path'], base64.b64decode(request['data'], validate=True), request['executable'])
        return True
    if action == 'seed':
        namespace = {}
        exec(request['script'], namespace)
        return namespace['seed_repository'](root / 'workspace', request['expected'])
    if action == 'service_directories':
        for relative in ('jobs',):
            (root / relative).mkdir(mode=0o700, exist_ok=True)
        return True
        return sorted(result)
    if action == 'file_refs':
        return file_refs(root, request['paths'])
    if action == 'mkdir':
        # The empty destination a later push_tree publishes into.
        directory, name = _parent_fd(root, request['path'], create_parents=True)
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory)
        finally:
            os.close(directory)
        return True
    if action == 'push_tree':
        push_tree(root, request['path'], request['refs'], sys.stdin.buffer)
        return True
    if action == 'pull_tree':
        relative = request.get('path')
        source = root if relative is None else root.joinpath(*_relative_parts(relative))
        write_archive(source, request['refs'], sys.stdout.buffer)
        return None
    if action == 'pull':
        try:
            fd = open_file(root, request['path'], os.O_RDONLY)
        except FileNotFoundError:
            if request.get('optional', False):
                return None
            raise
        with os.fdopen(fd, 'rb') as stream:
            prefix = request.get('prefix_size')
            if prefix is None:
                file_info(stream.fileno(), sys.stdout.buffer)
            else:
                info = os.fstat(stream.fileno())
                if type(prefix) is not int or prefix < 0 or not stat.S_ISREG(info.st_mode) or info.st_size < prefix:
                    raise ValueError('Invalid observation byte range')
                remaining = prefix
                while remaining:
                    chunk = stream.read(min(remaining, CHUNK_BYTES))
                    if not chunk:
                        raise ValueError('Observation prefix changed during collection')
                    sys.stdout.buffer.write(chunk)
                    remaining -= len(chunk)
        return None
    if action == 'status':
        try:
            data = read_file(root, request['path'] + '/result.json', 16384)
        except FileNotFoundError:
            data = b''
        return {'result': json.loads(data) if data else None}
    if action == 'read':
        try:
            return base64.b64encode(read_file(root, request['path'], request.get('limit'))).decode()
        except FileNotFoundError:
            if request.get('optional', False):
                return None
            raise
    if action == 'list':
        relative = request['path']
        if relative != 'workspace':
            raise ValueError('invalid tree')
        return file_list(root / relative, request['exclude'])
    if action == 'remove':
        shutil.rmtree(root)
        return True
    raise ValueError('unknown worker file operation')


def main():
    # An active native process must not use this bridge's /proc/PID/root or memory
    # to access the controller's filesystem view or the credential transfer.
    if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:
        raise OSError('could not disable file bridge process inspection')
    root = Path(sys.argv[1])
    if not root.is_absolute() or root.parent.is_symlink():
        raise ValueError('absolute worker root required')
    request = json.loads(sys.stdin.buffer.readline())
    result = dispatch(root, request)
    if request['action'] not in {'pull', 'pull_tree'}:
        print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
