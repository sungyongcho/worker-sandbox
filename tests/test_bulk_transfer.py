"""Bulk transfer integrity, staging and standard-library bridge boundaries."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from benchkit import worker_files


def ref(path, content=b'', *, executable=False, target=None):
    raw = content if target is None else target.encode()
    return {'path': path, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
            'size': len(raw), 'executable': executable,
            'kind': 'file' if target is None else 'symlink', 'target': target}


class BulkTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()

    def archive(self, refs=None):
        refs = worker_files.file_list(self.source, False) if refs is None else refs
        output = BytesIO()
        worker_files.write_archive(self.source, refs, output)
        return refs, output.getvalue()

    def test_bytes_permissions_long_names_and_link_targets_are_preserved(self):
        (self.source / 'binary').write_bytes(bytes(range(256)) * 9)
        (self.source / 'command').write_text('#!/bin/sh\nexit 0\n')
        (self.source / 'command').chmod(0o711)
        long_name = 'nested/' + '한글' * 35
        (self.source / 'nested').mkdir()
        (self.source / long_name).write_text('long Unicode path')
        (self.source / 'nested/relative').symlink_to('../command')
        (self.source / 'outside').symlink_to('/definitely/missing/' + 'x' * 150)
        (self.source / 'missing').symlink_to('../missing')
        refs, raw = self.archive()
        with tarfile.open(fileobj=BytesIO(raw), mode='r:') as archive:
            members = {member.name: member for member in archive}
            self.assertEqual(set(members), {item['path'] for item in refs})
            self.assertTrue(members['outside'].issym())
            self.assertEqual(members['outside'].linkname, os.readlink(self.source / 'outside'))
            self.assertEqual(archive.extractfile(long_name).read(), b'long Unicode path')
        destination = self.root / 'stage'
        worker_files.read_archive(BytesIO(raw), destination, refs)
        self.assertEqual(worker_files.file_list(destination, False), refs)
        self.assertEqual((destination / 'command').stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / 'binary').stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.readlink(destination / 'nested/relative'), '../command')
        self.assertEqual(self.archive(refs)[1], raw)

    def test_existing_exclusions_and_explicit_evidence_selection_are_unchanged(self):
        for name in ('.git', '.venv', 'node_modules', '.pytest-tmp', 'source'):
            (self.source / name).mkdir()
            (self.source / name / 'file').write_text(name)
        os.mkfifo(self.source / 'ipc')
        refs = worker_files.file_list(self.source, True)
        self.assertEqual([item['path'] for item in refs], ['.pytest-tmp/file', 'source/file'])
        raw = self.archive(refs)[1]
        worker_files.read_archive(BytesIO(raw), self.root / 'stage', refs)
        self.assertEqual(worker_files.file_list(self.root / 'stage', False), refs)
        explicit = worker_files.file_refs(self.source, ['.git/file', '.venv/file'])
        self.assertEqual([item['path'] for item in explicit], ['.git/file', '.venv/file'])
        raw = self.archive(explicit)[1]
        worker_files.read_archive(BytesIO(raw), self.root / 'evidence', explicit)
        self.assertEqual((self.root / 'evidence/.git/file').read_text(), '.git')
        with self.assertRaises(ValueError):
            worker_files.file_refs(self.source, ['ipc'])

    def test_large_payload_is_streamed_with_bounded_memory_and_short_writes(self):
        with (self.source / 'large').open('wb') as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        refs = worker_files.file_list(self.source, False)

        class BoundedStream:
            def __init__(self, stream):
                self.stream = stream

            def write(self, raw):
                if len(raw) > worker_files.CHUNK_BYTES:
                    raise AssertionError('unbounded write')
                return self.stream.write(raw[:8191])

            def read(self, size):
                if not 0 < size <= worker_files.CHUNK_BYTES:
                    raise AssertionError('unbounded read')
                return self.stream.read(min(size, 8189))

        with tempfile.TemporaryFile() as archive:
            stream = BoundedStream(archive)
            tracemalloc.start()
            try:
                worker_files.write_archive(self.source, refs, stream)
                archive.seek(0)
                worker_files.read_archive(stream, self.root / 'stage', refs)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertEqual(worker_files.file_list(self.root / 'stage', False), refs)

    def test_invalid_manifests_are_rejected_before_creating_staging(self):
        bad = [[ref(path)] for path in ('/escape', '../escape', 'a/../escape', 'a//b', './a',
                                       'a/', 'a\\b', '', 'nul\0path')]
        bad.extend(([ref('same'), ref('same')], [ref('a'), ref('a/b')],
                    [ref('a', target='../outside'), ref('a/b')],
                    [{**ref('file'), 'size': -1}], [{**ref('file'), 'size': True}],
                    [{**ref('file'), 'executable': 1}], [{**ref('file'), 'kind': 'fifo'}],
                    [{**ref('file'), 'digest': 'sha256:wrong'}],
                    [{**ref('file', target='elsewhere'), 'digest': ref('empty')['digest']}]))
        for refs in bad:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                worker_files.read_archive(BytesIO(b''), self.root / 'stage', refs)
            self.assertFalse((self.root / 'stage').exists())

    def test_source_and_destination_parent_links_are_never_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'secret')
        (self.source / 'parent').symlink_to(outside, target_is_directory=True)
        (self.source / 'file').symlink_to(outside / 'file')
        for path in ('parent/file', 'file'):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                worker_files.write_archive(self.source, [ref(path, b'secret')], BytesIO())
        (self.root / 'alias').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            worker_files.read_archive(BytesIO(b''), self.root / 'alias/stage', [])
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ['file'])

    def test_tampering_truncation_extra_members_and_special_types_are_rejected(self):
        (self.source / 'file').write_bytes(b'payload')
        refs, raw = self.archive()
        bad = [raw[:index] for index in (0, 1, 511, 512, 515, 1024, len(raw) - 1024, len(raw) - 1)]
        for offset in (100, 512, 519, 1024, len(raw) - 1):
            changed = bytearray(raw)
            changed[offset] ^= 1
            bad.append(bytes(changed))
        bad.extend((raw + b'x', raw[:1024] + raw))
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.BLKTYPE):
            member = tarfile.TarInfo('file')
            member.type, member.mode, member.linkname = kind, 0o600, '../outside'
            bad.append(member.tobuf(format=tarfile.PAX_FORMAT) + raw[512:])
        for index, archive in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(ValueError):
                worker_files.read_archive(BytesIO(archive), self.root / 'stage', refs)
            self.assertFalse((self.root / 'stage').exists())

    def test_source_identity_changes_fail_instead_of_producing_successful_transfer(self):
        path = self.source / 'file'
        path.write_bytes(b'original')
        refs = worker_files.file_list(self.source, False)
        for raw, mode in ((b'different-size', 0o600), (b'changed!', 0o600), (b'original', 0o700)):
            path.write_bytes(raw)
            path.chmod(mode)
            with self.subTest(raw=raw, mode=mode), self.assertRaises(ValueError):
                worker_files.write_archive(self.source, refs, BytesIO())
        path.write_bytes(b'x' * (worker_files.CHUNK_BYTES + 1))
        refs = worker_files.file_list(self.source, False)
        original = worker_files.file_chunks

        def mutate(fd, limit=None):
            for chunk in original(fd, limit):
                yield chunk
                path.write_bytes(b'changed during transfer')

        with patch.object(worker_files, 'file_chunks', side_effect=mutate), self.assertRaises(ValueError):
            worker_files.write_archive(self.source, refs, BytesIO())
        (self.source / 'link').symlink_to('old-target')
        refs = worker_files.file_refs(self.source, ['link'])
        (self.source / 'link').unlink()
        (self.source / 'link').symlink_to('new-target')
        with self.assertRaises(ValueError):
            worker_files.write_archive(self.source, refs, BytesIO())

    def test_push_publishes_only_complete_tree_and_never_overwrites_existing_data(self):
        (self.source / 'first').write_bytes(b'first')
        (self.source / 'second').write_bytes(b'second')
        refs, raw = self.archive()
        worker = self.root / 'worker'
        worker.mkdir()
        destination = worker / 'workspace'
        destination.mkdir()
        identity = destination.stat().st_ino
        evidence = worker / 'preserved-evidence'
        evidence.write_bytes(b'original attempt')
        with self.assertRaises(ValueError):
            worker_files.push_tree(worker, 'workspace', refs, BytesIO(raw[:1538]))
        self.assertEqual(destination.stat().st_ino, identity)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(sorted(path.name for path in worker.iterdir()), ['preserved-evidence', 'workspace'])
        worker_files.push_tree(worker, 'workspace', refs, BytesIO(raw))
        self.assertEqual(worker_files.file_list(destination, False), refs)
        with self.assertRaisesRegex(ValueError, 'empty'):
            worker_files.push_tree(worker, 'workspace', refs, BytesIO(raw))
        with self.assertRaises(FileExistsError):
            worker_files.read_archive(BytesIO(raw), destination, refs)
        self.assertEqual(worker_files.file_list(destination, False), refs)
        self.assertEqual(evidence.read_bytes(), b'original attempt')

    def test_empty_tree_and_destination_changes_during_transfer(self):
        refs, raw = self.archive()
        worker_files.read_archive(BytesIO(raw), self.root / 'empty', refs)
        self.assertEqual(list((self.root / 'empty').iterdir()), [])
        with tarfile.open(fileobj=BytesIO(raw), mode='r:') as archive:
            self.assertEqual(archive.getmembers(), [])
        worker = self.root / 'worker'
        destination = worker / 'workspace'
        destination.mkdir(parents=True)

        class ChangedDestination(BytesIO):
            def read(self, size):
                (destination / 'new-native-result').write_bytes(b'preserve me')
                return super().read(size)

        with self.assertRaises(OSError):
            worker_files.push_tree(worker, 'workspace', refs, ChangedDestination(raw))
        self.assertEqual((destination / 'new-native-result').read_bytes(), b'preserve me')
        self.assertEqual(sorted(path.name for path in worker.iterdir()), ['workspace'])

    def test_isolated_stdlib_bridge_accepts_large_manifest_and_binary_tree_actions(self):
        for index in range(600):
            (self.source / f'evidence-{index:04d}').write_bytes(str(index).encode())
        refs, raw = self.archive()
        worker = self.root / 'worker'
        (worker / 'workspace').mkdir(parents=True)
        script = Path(worker_files.__file__).read_text()

        def bridge(request, payload=b''):
            control = json.dumps(request).encode() + b'\n'
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(worker)],
                                    input=control + payload, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return result.stdout

        request = {'action': 'push_tree', 'path': 'workspace', 'refs': refs}
        self.assertGreater(len(json.dumps(request)), worker_files.CHUNK_BYTES)
        self.assertTrue(json.loads(bridge(request, raw)))
        returned = bridge({'action': 'pull_tree', 'path': 'workspace', 'refs': refs})
        self.assertEqual(returned, raw)
        explicit = json.loads(bridge({'action': 'file_refs', 'paths': ['workspace/evidence-0001']}))
        returned = bridge({'action': 'pull_tree', 'refs': explicit})
        worker_files.read_archive(BytesIO(returned), self.root / 'evidence', explicit)
        self.assertEqual((self.root / 'evidence/workspace/evidence-0001').read_bytes(), b'1')
