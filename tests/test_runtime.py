"""Native transport boundaries; subprocess tests need neither root nor accounts."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import socket
import tracemalloc
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import msgspec

from worker_sandbox import contracts as c, worker_files, worker_job, worker_service
from worker_sandbox.artifacts import tree_manifest
from worker_sandbox.profiles import claude, codex, generic
from worker_sandbox.runtime import NETWORK_BROKER, NativeRuntime
from fixtures import codex_auth


def recorder_script():
    """Test the stream recorder without pretending to be in a privileged namespace."""
    return ("import sys; scope={'__name__': 'recorder_test'}; "
            f"exec({Path(worker_job.__file__).read_text()!r}, scope); "
            "scope['run_job'](scope['Path'](sys.argv[1]))")


class WorkerFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'run'
        worker_files.dispatch(self.root, {'action': 'init'})

    def test_regular_file_identity_and_mode(self):
        worker_files.put_file(self.root, 'workspace/a/b', b'hello', True)
        self.assertEqual(worker_files.read_file(self.root, 'workspace/a/b', 5), b'hello')
        refs = worker_files.file_list(self.root / 'workspace', False)
        self.assertEqual(refs[0]['digest'], c.digest(b'hello'))
        self.assertTrue(refs[0]['executable'])

    def test_fifo_symlink_and_escape_never_read(self):
        os.mkfifo(self.root / 'workspace/fifo')
        (self.root / 'workspace/link').symlink_to('/etc/passwd')
        (self.root / 'workspace/dirlink').symlink_to('/etc', target_is_directory=True)
        for name in ('workspace/fifo', 'workspace/link', 'workspace/dirlink/passwd', '../etc/passwd', '/etc/passwd'):
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                worker_files.read_file(self.root, name, 20)
        rows = worker_files.file_list(self.root / 'workspace', False)
        self.assertEqual({row['kind'] for row in rows}, {'symlink'})
        observed = worker_files.observe_tree(self.root / 'workspace')
        self.assertTrue(any(row.get('kind') == 'runtime' for row in observed['entries']))

    def test_existing_file_cannot_be_overwritten(self):
        worker_files.put_file(self.root, 'workspace/a', b'first', False)
        with self.assertRaises(FileExistsError):
            worker_files.put_file(self.root, 'workspace/a', b'second', False)
        self.assertEqual(worker_files.read_file(self.root, 'workspace/a', 20), b'first')

    def test_bridge_runs_without_controller_dependency(self):
        result = subprocess.run([sys.executable, '-I', '-c', Path(worker_files.__file__).read_text(), str(self.root)],
                                input=b'{"action":"list","path":"workspace","exclude":false}',
                                capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])

    def test_file_listing_never_exposes_a_native_home(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'home/.codex').mkdir(parents=True)
            (root / 'home/.codex/auth.json').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'invalid tree'):
                worker_files.dispatch(root, {'action': 'list', 'path': 'home', 'exclude': False})


class WorkerJobTests(unittest.TestCase):
    def test_entrypoint_refuses_unisolated_execution_before_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            marker = folder / 'native-started'
            request = {'host_mount_namespace': os.readlink('/proc/self/ns/mnt'),
                       'argv': [sys.executable, '-c', f'open({str(marker)!r}, "w").close()']}
            (folder / 'request.json').write_text(json.dumps(request))
            result = subprocess.run([sys.executable, '-I', '-c', Path(worker_job.__file__).read_text(), str(folder)],
                                    capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'mount namespace was not isolated', result.stderr)
            self.assertFalse(marker.exists())

    def test_isolation_guard_checks_readonly_mounts_paths_and_privileges(self):
        job = {'host_mount_namespace': 'host', 'protected_paths': ['/private']}
        with patch('worker_sandbox.worker_job.os.readlink', return_value='worker'), \
             patch('worker_sandbox.worker_job.os.statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)), \
             patch('worker_sandbox.worker_job.os.access', return_value=False), \
             patch('worker_sandbox.worker_job.Path.read_text', return_value='NoNewPrivs:\t1\nCapEff:\t0000\n'):
            worker_job.verify_isolation(job)
            with patch('worker_sandbox.worker_job.os.statvfs', return_value=SimpleNamespace(f_flag=0)), \
                 self.assertRaisesRegex(RuntimeError, 'read-only'):
                worker_job.verify_isolation(job)
            with patch('worker_sandbox.worker_job.os.access', return_value=True), \
                 self.assertRaisesRegex(RuntimeError, 'protected controller'):
                worker_job.verify_isolation(job)
            with patch('worker_sandbox.worker_job.Path.read_text', return_value='NoNewPrivs:\t0\nCapEff:\t0000\n'), \
                 self.assertRaisesRegex(RuntimeError, 'privileges'):
                worker_job.verify_isolation(job)

    def job(self, code, *, stdin=b'input'):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        request = {'argv': [sys.executable, '-I', '-c', code], 'workspace': str(folder),
                   'environment': {'PATH': '/usr/bin:/bin'},
                   'stdin': base64.b64encode(stdin).decode()}
        (folder / 'request.json').write_text(json.dumps(request))
        proc = subprocess.Popen([sys.executable, '-I', '-c', recorder_script(), str(folder)],
                                start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def finish():
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=3)
        self.addCleanup(finish)
        proc.communicate(timeout=4)
        return json.loads((folder / 'result.json').read_text()), folder

    def test_output_stdin_exit_and_environment(self):
        result, folder = self.job("import os,sys; print(sys.stdin.read()); print(sorted(os.environ)); print('err',file=sys.stderr)")
        self.assertEqual(result['outcome'], 'completed')
        self.assertIn(b'input', (folder / 'stdout').read_bytes())
        self.assertNotIn(b'JEV_API_KEY', (folder / 'stdout').read_bytes())
        self.assertEqual((folder / 'stderr').read_bytes(), b'err\n')
        self.assertEqual(self.job('raise SystemExit(7)')[0]['outcome'], 'provider_error')

    def test_large_output_is_complete(self):
        result, folder = self.job("import os; block=b'x'*65536; [os.write(1,block) for _ in range(1040)]; os.write(2,b'end')")
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((folder / 'stdout').stat().st_size, 1040 * 65536)
        self.assertEqual((folder / 'stderr').read_bytes(), b'end')

    def test_background_server_does_not_block_next_turn(self):
        code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); print('done')"
        before = time.monotonic()
        result, _ = self.job(code)
        self.assertEqual(result['outcome'], 'completed')
        self.assertLess(time.monotonic() - before, 1.5)

    def test_worker_waits_for_completion_and_records_elapsed_time(self):
        result, folder = self.job("import time; time.sleep(.2); open('executed','w').write('done')")
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual((folder / 'executed').read_text(), 'done')
        self.assertGreaterEqual(result['wall_sec'], .2)
        self.assertGreaterEqual(result['finished_monotonic'] - result['started_monotonic'], .2)


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ('a' * 32)
        self.run.mkdir(); (self.run / 'workspace').mkdir(); (self.run / 'artifacts').mkdir()
        self.runtime = NativeRuntime(c.RuntimeSpec(), self.run, generic('/usr/bin/native'))
        self.runtime.remote = self.root / 'worker' / self.run.name
        self.runtime.remote.parent.mkdir()
        self.runtime.control = self.root / 'control'; self.runtime.control.mkdir(mode=0o700)
        self.runtime.lease = self.runtime.control / 'lease.json'
        self.rpc = patch.object(self.runtime, 'rpc', side_effect=lambda action, **args:
                                worker_files.dispatch(self.runtime.remote, {'action': action, **args}))
        self.rpc.start(); self.addCleanup(self.rpc.stop)
        bridge = patch.object(self.runtime, "bridge_command", side_effect=lambda:
                              [sys.executable, "-I", "-c", Path(worker_files.__file__).read_text(), str(self.runtime.remote)])
        bridge.start(); self.addCleanup(bridge.stop)

    def request(self, **fields):
        return c.make(c.RuntimeRequest, spec=self.runtime.spec, name='bk-' + 'b' * 32,
                      workspace=str(self.run / 'workspace'), home=str(self.run / 'home'),
                      argv=('/usr/bin/native',), log_dir=str(self.run / 'raw/job'), **fields)

    def claim(self):
        if not (self.runtime.run / 'workspace/.git').exists():
            from worker_sandbox.seed import seed_repository
            seed_repository(self.runtime.run / 'workspace')
        with patch.object(self.runtime, 'inspect'), patch.object(self.runtime, 'confirm_stopped', return_value=True):
            self.runtime.claim()


    def home_files(self):
        """The implementer HOME's regular files, read directly from the worker tree."""
        home = self.runtime.remote / 'home'
        return sorted(path.relative_to(home).as_posix() for path in home.rglob('*') if path.is_file()) if home.exists() else []

    def seed(self):
        (self.run / 'workspace/seed.txt').write_bytes(b'original')
        expected = tree_manifest(self.run / 'workspace', exclude_generated=False)
        self.claim()
        return expected

    def test_existing_lease_preserves_native_credentials_without_reseeding(self):
        self.runtime.agent = codex('/usr/bin/native')
        (self.runtime.control / 'credentials/codex/.codex').mkdir(parents=True)
        (self.runtime.control / 'credentials/codex/.codex/auth.json').write_bytes(codex_auth())
        self.claim()
        path = self.runtime.remote / 'home/.codex/auth.json'
        self.assertEqual(path.read_bytes(), codex_auth())
        path.write_bytes(codex_auth(token='native-refresh'))
        self.claim()
        self.assertEqual(path.read_bytes(), codex_auth(token='native-refresh'))

    def test_home_dir_is_seeded_into_codex_home_only_and_verified(self):
        files = {'.codex/config.toml': b'[agents]\nmax_threads = 2\n', '.codex/agents/reviewer.toml': b'model = "fixture"\n'}
        home_dir = self.root / 'home-dir'
        for path, data in files.items():
            (home_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (home_dir / path).write_bytes(data)
        self.runtime.agent = codex('/usr/bin/native')
        (self.runtime.control / 'credentials/codex/.codex').mkdir(parents=True)
        (self.runtime.control / 'credentials/codex/.codex/auth.json').write_bytes(codex_auth())
        self.runtime.home_dir = home_dir
        expected = self.seed()
        self.runtime.verify_seed(expected)
        self.assertEqual(self.home_files(),
                         sorted(['.codex/auth.json', '.codex/agents/reviewer.toml', '.codex/config.toml']))
        for path, data in files.items():
            self.assertEqual(self.runtime.read('home/' + path), data)
        self.assertEqual(self.runtime.read('home/.codex/auth.json'), codex_auth())

    def test_grader_runtime_does_not_seed_native_credentials(self):
        self.runtime.agent = codex('/usr/bin/native')
        (self.runtime.control / 'credentials/codex/.codex').mkdir(parents=True)
        (self.runtime.control / 'credentials/codex/.codex/auth.json').write_bytes(codex_auth())
        self.runtime.authentication = False
        self.claim()
        self.assertEqual(self.home_files(), [])

    def test_network_broker_uses_controller_resolver_and_preserves_host_denial(self):
        parent = self.root / 'runs with: "quotes" \\literal %n'
        parent.mkdir(mode=0o700)
        self.run = self.run.rename(parent / self.run.name)
        self.runtime.run = self.run
        self.claim()
        hosts = ('127.0.0.1', '192.0.2.10', '::1', '2001:db8::10', 'fe80::10')
        addresses = [{'addr_info': [{'local': host} for host in hosts]}]
        commands = []

        def command(argv, **kwargs):
            if argv == ['/usr/sbin/ip', '-j', 'address']:
                return json.dumps(addresses).encode()
            self.assertEqual(argv[:3], ['/usr/bin/sudo', '-n', '/usr/bin/systemd-run'])
            commands.append(argv)
            return b''

        is_file = Path.is_file
        with patch.object(Path, 'is_file', lambda path: path == Path('/usr/bin/slirp4netns') or is_file(path)), \
             patch('worker_sandbox.runtime.external_nameservers', return_value=['1.1.1.1', '2606:4700:4700::1111']), \
             patch('worker_sandbox.runtime.checked_command', side_effect=command), \
             patch.object(self.runtime, 'state', return_value={'MainPID': '1234'}):
            self.runtime.start_run()
        broker, = [argv for argv in commands if '/usr/bin/slirp4netns' in argv]
        supervisor, = [argv for argv in commands if '--property=PrivateNetwork=yes' in argv]
        self.assertEqual([arg for arg in supervisor if arg.startswith('--property=BindReadOnlyPaths=')],
                         [f'--property=BindReadOnlyPaths={self.runtime.remote / "resolv.conf"}:/etc/resolv.conf'])
        resolver = self.run / 'artifacts/upstream-resolv.conf'
        self.assertEqual(resolver.read_bytes(), b'nameserver 1.1.1.1\nnameserver 2606:4700:4700::1111\n')
        self.assertEqual(resolver.stat().st_uid, os.getuid())
        self.assertEqual(resolver.stat().st_mode & 0o777, 0o444)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.runtime.remote / 'upstream-resolv.conf').exists())
        self.assertEqual((self.runtime.remote / 'resolv.conf').stat().st_mode & 0o777, 0o600)
        mount, = [arg for arg in broker if arg.startswith('--property=BindReadOnlyPaths=')]
        self.assertEqual(mount, '--property=BindReadOnlyPaths="' + str(self.root)
                         + '/runs with: \\"quotes\\" \\\\literal %n/' + self.run.name
                         + '/artifacts/upstream-resolv.conf":/etc/resolv.conf')
        deny, = [arg.split('=', 2)[2] for arg in broker if arg.startswith('--property=IPAddressDeny=')]
        # Mixed transient properties accept numeric prefixes, not embedded aliases.
        networks = [ipaddress.ip_network(word) for word in deny.split()]
        for host in (*hosts, '127.0.0.0', '127.255.255.255'):
            self.assertTrue(any(ipaddress.ip_address(host) in network for network in networks), host)
        for destination in ('1.1.1.1', '2606:4700:4700::1111'):
            self.assertFalse(any(ipaddress.ip_address(destination) in network for network in networks))
        self.assertIn('--disable-host-loopback', broker)
        self.assertIn('--enable-sandbox', broker)
        self.assertIn('--enable-seccomp', broker)
        self.assertIn('--property=MountFlags=slave', broker)
        self.assertIn('--property=Type=notify', broker)
        for command in (broker, supervisor):
            self.assertIn('--property=RuntimeMaxSec=infinity', command)
        self.assertFalse((self.runtime.remote / 'service.json').exists())
        self.assertIn('--property=TimeoutStartSec=30s', broker)

    def test_broker_resolver_collision_never_overwrites_or_starts_services(self):
        self.claim()
        resolver = self.run / 'artifacts/upstream-resolv.conf'
        resolver.write_bytes(b'existing evidence')
        is_file = Path.is_file
        with patch.object(Path, 'is_file', lambda path: path == Path('/usr/bin/slirp4netns') or is_file(path)), \
             patch('worker_sandbox.runtime.external_nameservers', return_value=['1.1.1.1']), \
             patch('worker_sandbox.runtime.checked_command') as command, \
             self.assertRaises(FileExistsError):
            self.runtime.start_run()
        command.assert_not_called()
        self.assertFalse(self.runtime.service_active)
        self.assertEqual(resolver.read_bytes(), b'existing evidence')
        self.assertEqual(list((self.run / 'artifacts').glob('.upstream-resolv.conf.*')), [])

    def broker_process(self, address):
        binary = self.root / 'synthetic-broker'
        binary.write_text('#!' + sys.executable + '\n' + '''import os,sys
fd=int(sys.argv[1].split('=',1)[1])
mode=sys.stdin.buffer.read(1)
if mode == b'e': raise SystemExit(9)
os.write(fd,mode)
os.close(fd)
if mode == b'1': sys.stdin.buffer.read(1)
raise SystemExit(7)
''')
        binary.chmod(0o700)
        process = subprocess.Popen([sys.executable, '-I', '-c', NETWORK_BROKER, str(binary)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={'PATH': '/usr/bin:/bin', 'NOTIFY_SOCKET': address}, start_new_session=True)
        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, 9)
            process.communicate(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_broker_readiness_waits_for_native_signal_and_preserves_exit(self):
        for abstract in (False, True):
            address = ('@benchkit-' + self.root.name) if abstract else str(self.root / 'notify.sock')
            with self.subTest(abstract=abstract), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.bind('\0' + address[1:] if abstract else address)
                process = self.broker_process(address)
                notify.setblocking(False)
                with self.assertRaises(BlockingIOError):
                    notify.recv(128)
                process.stdin.write(b'1'); process.stdin.flush()
                notify.settimeout(3)
                self.assertEqual(notify.recv(128), b'READY=1')
                self.assertIsNone(process.poll())
                process.communicate(b'x', timeout=3)
                self.assertEqual(process.returncode, 7)

    def test_failed_or_invalid_native_readiness_never_notifies(self):
        for mode in (b'e', b'0'):
            address = str(self.root / ('notify-' + mode.decode()))
            with self.subTest(mode=mode), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.bind(address)
                process = self.broker_process(address)
                _, error = process.communicate(mode, timeout=3)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn(b'Network broker exited before reporting readiness', error)
                notify.setblocking(False)
                with self.assertRaises(BlockingIOError):
                    notify.recv(128)

    def test_workspace_links_are_listed_without_following(self):
        self.claim()
        (self.runtime.remote / 'workspace/apply_patch').symlink_to('/different/target')
        refs = self.runtime.files('workspace')
        self.assertEqual([(ref.kind, ref.target) for ref in refs], [('symlink', '/different/target')])

    def test_streamed_transfer_above_former_file_limit_uses_bounded_memory(self):
        source = self.run / 'workspace/large'
        with source.open('wb') as stream:
            stream.truncate(21 * 1024 * 1024)
        tracemalloc.start()
        try:
            self.claim()
            ref = self.runtime.download('workspace/large', self.run / 'returned')
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(ref.size, source.stat().st_size)
        self.assertLess(peak, 4 * 1024 * 1024)
        self.assertEqual(ref.digest, self.runtime.files('workspace')[0].digest)

    def test_failed_stream_producer_never_publishes_partial_artifact(self):
        target = self.run / 'partial'
        failing = [sys.executable, '-I', '-c',
                   "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(b'partial'); sys.exit(7)"]
        with patch.object(self.runtime, 'bridge_command', return_value=failing):
            with self.assertRaisesRegex(RuntimeError, 'transfer failed'):
                self.runtime.download('workspace/result', target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.run.glob('.partial.*')))

    def test_log_storage_failure_is_infrastructure_and_preserves_provider_error(self):
        self.claim()
        for outcome, code in (('completed', 0), ('provider_error', 7)):
            with self.subTest(outcome=outcome):
                observed = {'outcome': outcome, 'exit_code': code, 'error': None}
                request = msgspec.structs.replace(self.request(), name='bk-' + ('c' if code else 'd') * 32,
                                                  log_dir=str(self.run / 'raw' / outcome))
                with patch.object(self.runtime, '_observe', return_value=observed), \
                     patch('worker_sandbox.runtime.checked_command', return_value=b''), \
                     patch.object(self.runtime, 'cleanup', return_value=True), \
                     patch.object(self.runtime, 'download', side_effect=OSError('disk full')):
                    result = self.runtime.invoke(request)
                self.assertEqual(result.outcome, 'harness_error' if outcome == 'completed' else outcome)
                self.assertEqual((result.exit_code, result.cleanup), (code, 'confirmed'))
                self.assertIn('disk full', result.error)
                self.assertIsNone(result.stdout_path)

    def test_native_observer_failure_preserves_streams_and_cleanup(self):
        self.claim()
        for index, failure in enumerate((c.Halt('provider_error', 'native provider failed'),
                                        c.Halt('protocol_error', 'invalid native stream'),
                                        c.Halt('cancelled', 'owner cancelled'),
                                        RuntimeError('unexpected observer failure'))):
            with self.subTest(failure=failure):
                request = msgspec.structs.replace(self.request(), name='bk-' + f'{index:032x}',
                                                 log_dir=str(self.run / 'raw' / str(index)))
                def observe(request, relative, *_args, **_kwargs):
                    self.runtime.put(relative + '/stdout', b'original partial native output')
                    self.runtime.put(relative + '/stderr', b'original native stderr')
                    raise failure
                with patch.object(self.runtime, '_observe', side_effect=observe), \
                     patch('worker_sandbox.runtime.checked_command', return_value=b''), \
                     patch.object(self.runtime, 'cleanup', return_value=True) as cleanup:
                    result = self.runtime.invoke(request)
                self.assertEqual(result.outcome, failure.outcome if isinstance(failure, c.Halt) else 'harness_error')
                self.assertIn(str(failure), result.error)
                self.assertEqual(result.cleanup, 'confirmed')
                self.assertIsNone(result.exit_code)
                self.assertEqual(Path(result.stdout_path).read_bytes(), b'original partial native output')
                self.assertEqual(Path(result.stderr_path).read_bytes(), b'original native stderr')
                cleanup.assert_called_once_with(request.name)


    def test_seed_binding_rejects_changed_missing_extra_or_duplicate_files(self):
        expected = self.seed()
        actual = self.runtime.files('workspace')
        self.assertNotEqual(expected[0].source, actual[0].source)
        self.runtime.verify_seed(expected)
        changed = [(msgspec.structs.replace(expected[0], **{field: value}),) for field, value in (
            ('path', 'renamed.txt'), ('digest', c.digest(b'changed!')), ('size', 9), ('executable', True))]
        changed += [(), expected * 2, expected + (msgspec.structs.replace(expected[0], path='extra.txt'),)]
        for files in changed:
            with self.subTest(files=files), self.assertRaisesRegex(c.ContractError, 'worker seed changed'):
                self.runtime.verify_seed(files)
        (self.runtime.remote / 'workspace/seed.txt').write_bytes(b'changed!')
        with self.assertRaisesRegex(c.ContractError, 'worker seed changed'):
            self.runtime.verify_seed(expected)


    def test_global_lease_rejects_other_run_even_between_cli_commands(self):
        self.claim()
        self.runtime.run = self.root / ('c' * 32)
        with patch.object(self.runtime, 'inspect'), self.assertRaisesRegex(c.ContractError, 'another run'):
            self.runtime.claim()

    def test_live_account_blocks_fresh_run(self):
        with patch.object(self.runtime, 'inspect'), patch.object(self.runtime, 'confirm_stopped', return_value=False):
            with self.assertRaisesRegex(c.ContractError, 'still has processes'):
                self.runtime.claim()
        self.assertFalse(self.runtime.lease.exists())

    def test_systemd_owns_account_descendants_without_elapsed_time_or_resource_caps(self):
        command = self.runtime.service_command(self.request(), self.runtime.remote / 'jobs')
        for value in ('User=worker-sandbox', 'ExitType=cgroup', 'KillMode=control-group', 'RuntimeMaxSec=infinity'):
            self.assertIn('--property=' + value, command)
        self.assertFalse(any('CPUQuota' in item or 'MemoryMax' in item for item in command))
        self.assertNotIn('docker', ' '.join(command))

    def test_systemd_hides_private_host_state_and_restricts_persistent_writes(self):
        command = self.runtime.service_command(self.request(), self.runtime.remote / 'jobs')
        for value in ('ProtectHome=yes', 'ProtectSystem=strict', 'ProtectProc=invisible',
                      'PrivateDevices=yes', 'PrivateIPC=yes', 'CapabilityBoundingSet=',
                      'TemporaryFileSystem=/var:ro /run:ro /dev/shm:rw,mode=1777',
                      f'BindPaths={self.runtime.remote}', f'ReadWritePaths={self.runtime.remote}'):
            self.assertIn('--property=' + value, command)

    def test_setup_resolver_survives_hidden_host_target_and_is_archived(self):
        self.claim()
        target = self.root / 'host/run/systemd/resolve/stub-resolv.conf'
        target.parent.mkdir(parents=True)
        link = self.root / 'host/etc/resolv.conf'
        link.parent.mkdir(parents=True)
        link.symlink_to('../run/systemd/resolve/stub-resolv.conf')
        read_bytes = Path.read_bytes
        def host_read(path):
            return read_bytes(link if path == Path('/etc/resolv.conf') else path)
        for index in range(2):
            original = f'nameserver 127.0.0.53\nsearch setup-{index}.example\n'.encode()
            target.write_bytes(original)
            request = msgspec.structs.replace(self.request(), name='bk-' + f'{index:032x}')
            with patch.object(Path, 'read_bytes', host_read):
                relative = self.runtime._job(request)
            target.unlink()
            resolver = self.runtime.remote / relative / 'resolv.conf'
            self.assertEqual(resolver.read_bytes(), original)
            self.assertFalse(resolver.is_symlink())
            self.assertEqual(resolver.stat().st_mode & 0o777, 0o600)
            command = self.runtime.service_command(request, resolver.parent)
            mounts = [arg for arg in command if arg.startswith('--property=BindReadOnlyPaths=')]
            self.assertEqual(mounts, [f'--property=BindReadOnlyPaths={resolver}:/etc/resolv.conf'])
            self.assertIn('--property=TemporaryFileSystem=/var:ro /run:ro /dev/shm:rw,mode=1777', command)
            self.assertNotIn('--property=PrivateNetwork=yes', command)

    def test_missing_setup_resolver_prevents_dispatch(self):
        self.claim()
        read_bytes = Path.read_bytes
        def missing(path):
            if path == Path('/etc/resolv.conf'):
                raise FileNotFoundError('host resolver is unavailable')
            return read_bytes(path)
        with patch.object(Path, 'read_bytes', missing), \
             patch('worker_sandbox.runtime.checked_command') as launch, \
             self.assertRaisesRegex(FileNotFoundError, 'host resolver is unavailable'):
            self.runtime.invoke(self.request())
        launch.assert_not_called()
        self.assertFalse((self.runtime.remote / 'jobs' / self.request().name / 'ready').exists())

    def test_setup_resolver_collision_preserves_existing_evidence(self):
        self.claim()
        relative = 'jobs/' + self.request().name + '/resolv.conf'
        self.runtime.put(relative, b'existing evidence')
        with patch('worker_sandbox.runtime.checked_command') as launch, self.assertRaises(FileExistsError):
            self.runtime.invoke(self.request())
        launch.assert_not_called()
        self.assertEqual(self.runtime.read(relative), b'existing evidence')
        self.assertFalse((self.runtime.remote / 'jobs' / self.request().name / 'ready').exists())

    def test_active_run_keeps_private_resolver_without_reading_host_dns(self):
        self.claim()
        self.runtime.put('resolv.conf', b'nameserver 10.0.2.3\n')
        self.runtime.service_active = True
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('host DNS read during measured turn')):
            relative = self.runtime._job(self.request())
        self.assertFalse((self.runtime.remote / relative / 'resolv.conf').exists())
        self.assertEqual(self.runtime.read('resolv.conf'), b'nameserver 10.0.2.3\n')

    def test_request_cannot_bind_another_run_home_or_workspace(self):
        self.claim()
        for field, value in (('home', str(self.root / 'foreign/homes/swe')), ('home', str(self.run / 'homes/jev')),
                             ('workspace', str(self.root / 'foreign/workspace'))):
            with self.subTest(field=field), self.assertRaisesRegex(c.ContractError, 'another run'):
                self.runtime._job(msgspec.structs.replace(self.request(), **{field: value}))

    def test_residue_blocks_reclaim_and_does_not_delete_the_other_run(self):
        self.claim()
        residue = self.runtime.remote.parent / 'other-run'
        residue.mkdir(); (residue / 'marker').write_text('previous context')
        with self.assertRaisesRegex(ValueError, 'another run'):
            self.claim()
        with patch.object(self.runtime, 'confirm_stopped', return_value=True), self.assertRaises(c.ContractError):
            self.runtime.release()
        self.assertTrue(self.runtime.lease.exists())
        self.assertEqual((residue / 'marker').read_text(), 'previous context')

    def test_preexisting_native_cache_blocks_claim_before_lease_allocation(self):
        residue = self.runtime.remote.parent / '.cache/codex'
        residue.mkdir(parents=True)
        marker = residue / 'native-evidence'
        marker.write_bytes(b'original failed attempt')
        with self.assertRaisesRegex(c.ContractError, 'worker home is not empty'):
            self.claim()
        self.assertFalse(self.runtime.lease.exists())
        self.assertFalse(self.runtime.remote.exists())
        self.assertEqual(marker.read_bytes(), b'original failed attempt')
        self.assertNotIn('init', [call.args[0] for call in self.runtime.rpc.call_args_list])

    def test_implementer_receives_only_declared_environment(self):
        self.runtime.agent = claude('/usr/bin/native')
        self.claim()
        self.runtime.service_active = True
        with patch.dict(os.environ, {'BENCHKIT_ROLE_CHANNEL': '/personal/roles', 'BENCHKIT_PARENT_CALL': 'personal-call'}):
            for name in self.runtime.agent.credential_env:
                os.environ.pop(name, None)
            job = self.runtime._job(self.request())
        environment = json.loads(self.runtime.read(job + '/request.json'))['environment']
        self.assertNotIn('BENCHKIT_ROLE_CHANNEL', environment)
        self.assertNotIn('BENCHKIT_PARENT_CALL', environment)
        self.assertNotIn(self.runtime.worker_path('bin'), environment['PATH'].split(':'))
        home = self.runtime.worker_path('home')
        self.assertEqual(environment, {
            'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': home,
            'CLAUDE_CONFIG_DIR': home + '/.claude', 'XDG_CONFIG_HOME': home + '/.config',
            'XDG_DATA_HOME': home + '/.local/share', 'XDG_CACHE_HOME': home + '/.cache',
            'XDG_STATE_HOME': home + '/.local/state', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'TMPDIR': self.runtime.worker_path('tmp')})

    def test_login_imports_a_validated_credential_from_a_private_staging_home(self):
        from worker_sandbox import __main__ as cli
        profile = codex('/usr/bin/native')
        staging = self.runtime.control / 'credentials/codex'
        calls = []

        def agent_login(argv, *, cwd, env, check, stdout=None):
            calls.append(argv)
            self.assertEqual(Path(cwd), staging)
            self.assertEqual(Path(cwd).stat().st_mode & 0o777, 0o700)
            self.assertEqual((env['HOME'], env['CODEX_HOME']), (str(staging), str(staging) + '/.codex'))
            if argv == profile.login_argv:
                Path(env['CODEX_HOME']).mkdir(mode=0o700)
                (Path(env['CODEX_HOME']) / 'auth.json').write_bytes(codex_auth())
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0, stdout=b'Logged in using ChatGPT\n')

        with patch('worker_sandbox.__main__.hostconfig.read', return_value=c.RuntimeSpec(control_root=str(self.runtime.control))), \
             patch('worker_sandbox.__main__.subprocess.run', side_effect=agent_login), \
             patch.object(NativeRuntime, 'verify_model') as verify:
            result = cli.login(profile)
        self.assertEqual(result, {'profile': 'codex', 'status_exit_code': 0, 'status': 'Logged in using ChatGPT\n'})
        self.assertEqual(calls, [profile.login_argv, profile.status_argv])
        verify.assert_called_once_with('/usr/bin/native', None)
        for path in (staging.parent, staging):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((staging / profile.credential_files[0]).read_bytes(), codex_auth())
        self.assertEqual({path.name for path in self.runtime.control.iterdir()}, {'credentials', 'owner.lock'})
        self.runtime.agent = profile
        self.claim()
        self.assertEqual(self.home_files(), list(profile.credential_files))

    def test_login_failures_leave_the_vault_unchanged(self):
        from worker_sandbox import __main__ as cli
        profile = codex('/usr/bin/native')
        staging = self.runtime.control / 'credentials/codex'
        for path in (staging.parent, staging, staging / '.codex'):
            path.mkdir(mode=0o700)
        (staging / '.codex/auth.json').write_bytes(codex_auth())
        spec = c.RuntimeSpec(control_root=str(self.runtime.control))

        def staged():
            return sorted((path.relative_to(staging).as_posix(), path.read_bytes()) for path in staging.rglob('*') if path.is_file())

        before = staged()
        with patch('worker_sandbox.__main__.hostconfig.read', return_value=spec), \
             patch('worker_sandbox.__main__.subprocess.run', return_value=SimpleNamespace(returncode=1)) as run, \
             patch.object(NativeRuntime, 'verify_model'), self.assertRaisesRegex(c.ContractError, 'codex login failed'):
            cli.login(profile)
        self.assertEqual([call.args[0] for call in run.call_args_list], [profile.login_argv])
        self.assertEqual(staged(), before)
        self.claim()
        with patch('worker_sandbox.__main__.hostconfig.read', return_value=spec), \
             patch('worker_sandbox.__main__.subprocess.run') as run, \
             self.assertRaisesRegex(c.ContractError, 'outside any run'):
            cli.login(profile)
        run.assert_not_called()
        self.assertEqual(staged(), before)

    def test_invalid_binary_stops_login_before_any_subprocess(self):
        binary = (self.run / 'native-fixture').resolve()
        binary.write_bytes(b'fixture executable\n')
        binary.chmod(0o755)
        frozen = 'sha256:' + '0' * 64
        installation = {binary, *binary.parents}
        real_stat = Path.stat

        # Model installation metadata only; executable access and hashing use the real file.
        def installation_stat(path, *, follow_symlinks=True):
            info = real_stat(path, follow_symlinks=follow_symlinks)
            if path not in installation:
                return info
            fields = list(info)
            fields[0] = info.st_mode & ~0o022
            fields[4] = 0
            if path == invalid_path:
                fields[0] |= writable
                fields[4] = owner
            return os.stat_result(fields)

        cases = [(None, 0, 0, 'differs from frozen binary digest')]
        cases.extend((path, owner, writable, 'native installation must be root-owned')
                     for path in (binary, binary.parent)
                     for owner, writable in ((1234, 0), (0, 0o020), (0, 0o002)))
        for invalid_path, owner, writable, error in cases:
            with self.subTest(path=invalid_path, owner=owner, writable=writable), \
                 patch.object(Path, 'stat', installation_stat), \
                 patch('worker_sandbox.runtime.subprocess.run') as run:
                if invalid_path is None:
                    identity = c.digest(binary.read_bytes())
                    self.assertEqual(NativeRuntime.verify_model(str(binary), identity), identity)
                    self.assertEqual(NativeRuntime.verify_model(str(binary), None), identity)
                with self.assertRaisesRegex(c.ContractError, error):
                    NativeRuntime.verify_model(str(binary), frozen)
                run.assert_not_called()

    def test_private_environment_and_role_home(self):
        self.runtime.agent = codex('/usr/bin/native')
        self.claim()
        with patch.dict(os.environ, {'JEV_API_KEY': 'do-not-forward', 'CODEX_MODEL': 'personal'}):
            relative = self.runtime._job(self.request())
        job = json.loads(self.runtime.read(relative + '/request.json'))
        self.assertNotIn('JEV_API_KEY', job['environment'])
        self.assertNotIn('CODEX_MODEL', job['environment'])
        self.assertEqual(job['environment']['PATH'], '/usr/local/bin:/usr/bin:/bin')
        self.assertTrue(job['environment']['CODEX_HOME'].endswith('/home/.codex'))
        self.assertTrue(job['environment']['XDG_CONFIG_HOME'].endswith('/home/.config'))

    def test_failed_collection_preserves_previous_mirror(self):
        self.claim()
        (self.run / 'workspace/old').write_text('retained')
        worker_files.put_file(self.runtime.remote, 'workspace/nested/a', b'new', True)
        download = self.runtime.download_tree
        def changed(destination, refs, *, relative):
            (self.runtime.remote / 'workspace/nested/a').write_bytes(b'changed')
            return download(destination, refs, relative=relative)
        with patch.object(self.runtime, 'download_tree', side_effect=changed), self.assertRaises(c.ContractError):
            self.runtime.collect_workspace()
        self.assertEqual((self.run / 'workspace/old').read_text(), 'retained')
        (self.runtime.remote / 'workspace/nested/a').write_bytes(b'new')
        self.runtime.collect_workspace()
        self.assertEqual((self.run / 'workspace/nested/a').read_bytes(), b'new')
        self.assertTrue((self.run / 'workspace/nested/a').stat().st_mode & 0o111)
        self.assertFalse((self.run / 'workspace/old').exists())

    def test_bulk_bridge_count_does_not_grow_with_file_count(self):
        for index in range(300):
            (self.run / 'workspace' / str(index)).write_bytes(str(index).encode())
        with patch.object(self.runtime, 'bridge_command', wraps=self.runtime.bridge_command) as bridge:
            self.claim()
            self.assertEqual(bridge.call_count, 1)
            bridge.reset_mock()
            self.runtime.collect_workspace()
            self.assertEqual(bridge.call_count, 1)
        self.assertEqual(len(tree_manifest(self.run / 'workspace')), 300)

    def test_interrupted_mirror_publication_preserves_previous_tree(self):
        self.claim()
        (self.run / 'workspace/old').write_bytes(b'previous mirror')
        self.runtime.put('workspace/new', b'collected mirror')
        with patch('worker_sandbox.artifacts._rename_directory', side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            self.runtime.collect_workspace()
        self.assertEqual((self.run / 'workspace/old').read_bytes(), b'previous mirror')
        self.assertFalse((self.run / 'workspace/new').exists())

    def test_collection_recovers_after_controller_died_between_directory_renames(self):
        self.claim()
        (self.run / 'workspace').rename(self.run / '.interrupted-previous')
        worker_files.put_file(self.runtime.remote, 'workspace/result', b'recovered', False)
        self.runtime.collect_workspace()
        self.assertEqual((self.run / 'workspace/result').read_bytes(), b'recovered')

    def test_cleanup_refuses_foreign_unit(self):
        with patch.object(self.runtime, 'state', return_value={'User': 'other', 'LoadState': 'loaded'}):
            with self.assertRaisesRegex(c.ContractError, 'another account'):
                self.runtime.cleanup('bk-' + 'b' * 32)

    def test_unit_collected_between_inspect_and_stop_is_confirmed_absent(self):
        states = [{'User':'worker-sandbox', 'LoadState':'loaded'}, {'LoadState':'not-found'}]
        with patch.object(self.runtime, 'state', side_effect=states), \
             patch('worker_sandbox.runtime.checked_command', side_effect=RuntimeError('Unit not loaded')):
            self.assertTrue(self.runtime.cleanup('bk-' + 'b' * 32))

    def test_failed_stop_of_existing_unit_is_not_hidden(self):
        state = {'User':'worker-sandbox', 'LoadState':'loaded', 'ActiveState':'active'}
        with patch.object(self.runtime, 'state', return_value=state), \
             patch('worker_sandbox.runtime.checked_command', side_effect=RuntimeError('permission denied')):
            with self.assertRaisesRegex(RuntimeError, 'permission denied'):
                self.runtime.cleanup('bk-' + 'b' * 32)

    def test_release_requires_stopped_account_and_matching_lease(self):
        self.claim()
        with patch.object(self.runtime, 'confirm_stopped', return_value=False), self.assertRaises(c.ContractError):
            self.runtime.release()
        self.assertTrue(self.runtime.remote.exists())
        with patch.object(self.runtime, 'confirm_stopped', return_value=True):
            self.runtime.release()
            self.runtime.release()
        self.assertFalse(self.runtime.remote.exists())
        self.assertFalse(self.runtime.lease.exists())

    def test_invocation_failure_preserves_primary_reason_when_cleanup_fails(self):
        self.claim()
        request = self.request()
        with patch('worker_sandbox.runtime.checked_command', return_value=b''), \
             patch.object(self.runtime, '_observe', return_value={'outcome':'cancelled','exit_code':None,'error':'cancelled'}), \
             patch.object(self.runtime, 'cleanup', side_effect=RuntimeError('stop failed')):
            result = self.runtime.invoke(request)
        self.assertEqual((result.outcome, result.cleanup), ('cancelled', 'failed'))
        self.assertIn('stop failed', result.cleanup_error)

    def test_result_published_between_poll_and_service_exit_is_not_lost(self):
        terminal = {'outcome': 'completed', 'exit_code': 0, 'error': None}
        with patch.object(self.runtime, 'rpc', side_effect=[{'result': None}, {'result': terminal}]), \
             patch.object(self.runtime, 'state', return_value={'ActiveState':'inactive', 'Result':'success'}):
            result = self.runtime._observe(self.request(), 'jobs/test', None)
        self.assertEqual(result, terminal)

    def test_elapsed_time_does_not_stop_observation_but_cancellation_does(self):
        terminal = {'outcome': 'completed', 'exit_code': 0, 'error': None}
        with patch.object(self.runtime, 'rpc', side_effect=[{'result': None}, {'result': terminal}]), \
             patch.object(self.runtime, 'state', return_value={'ActiveState': 'active', 'MainPID': '1234'}), \
             patch('worker_sandbox.runtime.time.monotonic', return_value=10**12), \
             patch('worker_sandbox.runtime.time.sleep'):
            result = self.runtime._observe(self.request(), 'jobs/test', lambda: False)
        self.assertEqual(result, terminal)
        with patch.object(self.runtime, 'rpc') as rpc:
            result = self.runtime._observe(self.request(), 'jobs/test', lambda: True)
        self.assertEqual(result['outcome'], 'cancelled')
        rpc.assert_not_called()

    def test_external_service_failure_remains_a_harness_error(self):
        with patch.object(self.runtime, 'rpc', return_value={'result': None}), \
             patch.object(self.runtime, 'state', return_value={'ActiveState': 'failed', 'Result': 'signal'}):
            result = self.runtime._observe(self.request(), 'jobs/test', None)
        self.assertEqual(result['outcome'], 'harness_error')
        self.assertIn('signal', result['error'])

    def test_dead_main_process_or_broker_cannot_leave_an_unbounded_wait(self):
        alive = {'ActiveState': 'active', 'MainPID': '1234'}
        dead = {'ActiveState': 'active', 'MainPID': '0', 'Result': 'exit-code'}
        for active, failed in ((False, self.request().name), (True, self.runtime.run_unit),
                               (True, self.runtime.network_unit)):
            self.runtime.service_active = active
            def state(unit):
                return dead if unit == failed else alive
            with self.subTest(active=active, failed=failed), \
                 patch.object(self.runtime, 'rpc', return_value={'result': None}), \
                 patch.object(self.runtime, 'state', side_effect=state):
                result = self.runtime._observe(self.request(), 'jobs/test', None)
            self.assertEqual(result['outcome'], 'harness_error')
            self.assertIn(failed, result['error'])

    def test_only_active_run_owns_call_descendants(self):
        self.claim()
        self.runtime.rpc('service_directories')
        for active in (False, True):
            for index, outcome in enumerate(('completed', 'provider_error', 'cancelled', 'harness_error')):
                self.runtime.service_active = active
                request = msgspec.structs.replace(self.request(), name='bk-' + f'{index + 10 * active:032x}',
                    log_dir=str(self.run / 'raw' / f'{active}-{outcome}'))
                with self.subTest(active=active, outcome=outcome), \
                     patch('worker_sandbox.runtime.checked_command', return_value=b'') as launch, \
                     patch.object(self.runtime, '_observe', return_value={'outcome':outcome,'exit_code':0,'error':None}), \
                     patch.object(self.runtime, 'cleanup', return_value=True) as cleanup:
                    result = self.runtime.invoke(request)
                self.assertEqual(result.outcome, outcome)
                if active:
                    launch.assert_not_called()
                    cleanup.assert_not_called()
                    self.assertEqual(result.cleanup, 'pending')
                else:
                    launch.assert_called_once()
                    cleanup.assert_called_once_with(request.name)
                    self.assertEqual(result.cleanup, 'confirmed')



if __name__ == '__main__':
    unittest.main()
