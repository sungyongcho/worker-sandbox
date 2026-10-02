"""Rootless mode: spec fields, argv builders, nft rules and process state; no namespace is created."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import msgspec

from worker_sandbox import contracts as c, rootless
from worker_sandbox.profiles import claude, generic
from worker_sandbox.rootless import RootlessRuntime


class RuntimeSpecTests(unittest.TestCase):
    def test_root_mode_is_the_default_and_needs_no_bases(self):
        spec = c.RuntimeSpec()
        self.assertEqual((spec.mode, spec.subuid_base, spec.subgid_base), ('root', None, None))
        self.assertEqual(c.loads(b'{"account":"worker-sandbox","control_root":"/c","python":"/usr/bin/python3",'
                                 b'"worker_root":"/w"}', c.RuntimeSpec).mode, 'root')

    def test_rootless_mode_requires_both_bases(self):
        for fields in ({}, {'subuid_base': 100000}, {'subgid_base': 100000}):
            with self.subTest(fields=fields), self.assertRaisesRegex(c.ContractError, 'subordinate'):
                c.validate({'mode': 'rootless', **fields}, c.RuntimeSpec)
        spec = c.validate({'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000}, c.RuntimeSpec)
        self.assertEqual((spec.subuid_base, spec.subgid_base), (100000, 100000))

    def test_rootless_roots_stay_absolute_and_bases_nonnegative(self):
        base = {'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000}
        for fields in ({'worker_root': 'relative'}, {'control_root': 'relative'}, {'subuid_base': -1}, {'mode': 'other'}):
            with self.subTest(fields=fields), self.assertRaises(c.ContractError):
                c.validate({**base, **fields}, c.RuntimeSpec)


def spec(root, **fields):
    return c.validate({'account': 'tester', 'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000,
                       'control_root': str(root / 'control'), 'worker_root': str(root / 'worker'), **fields}, c.RuntimeSpec)


class RootlessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'control').mkdir(mode=0o700)
        self.run = self.root / 'out/runs' / ('a' * 32)
        (self.run / 'artifacts').mkdir(parents=True)
        self.runtime = RootlessRuntime(spec(self.root), self.run, claude('/usr/local/lib/agent/claude'))
        self.request = c.make(c.RuntimeRequest, spec=self.runtime.spec, name='bk-' + 'b' * 32,
                              workspace=str(self.run / 'workspace'), home=str(self.run / 'home'),
                              argv=('/usr/local/lib/agent/claude',), log_dir=str(self.run / 'raw/job'))
        self.map = [f'--map-users={os.getuid()},0,1', '--map-users=100000,1000,1',
                    f'--map-groups={os.getgid()},0,1', '--map-groups=100000,1000,1']

    def test_root_mode_spec_is_refused(self):
        with self.assertRaisesRegex(c.ContractError, 'rootless runtime spec'):
            RootlessRuntime(c.RuntimeSpec(), self.run, generic('/bin/true'))

    def test_bridge_runs_as_inner_1000_without_a_proc_mount(self):
        command = self.runtime.bridge_command()
        expected = ['/usr/bin/unshare', '--user', *self.map, '--mount', '--', '/usr/bin/setpriv', '--reuid', '1000',
                    '--regid', '1000', '--clear-groups', '--', '/usr/bin/python3', '-I', '-c']
        self.assertEqual(command[:len(expected)], expected)
        self.assertEqual(command[-1], str(self.runtime.remote))
        self.assertNotIn('--mount-proc', command)

    def test_run_service_bwrap_argv_is_in_the_fixed_order(self):
        command = self.runtime.service_command(self.request, self.runtime.remote, network=True)
        remote = str(self.runtime.remote)
        target = os.path.realpath('/etc/resolv.conf')
        target = target if target.startswith('/run/') else '/etc/resolv.conf'
        self.assertEqual(command, [
            '/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--die-with-parent',
            '--new-session', '--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/var', '--tmpfs', '/tmp',
            '--tmpfs', '/dev/shm', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/run',
            '--ro-bind', remote + '/resolv.conf', target, '--remount-ro', '/run', '--bind', remote, remote,
            '--remount-ro', '/var', '--', '/usr/bin/python3', '-I', remote + '/worker_service.py', remote])
        self.assertNotIn('--unshare-net', command)

    def test_read_only_paths_and_a_binary_under_home_are_bound_read_only(self):
        self.runtime.agent = claude('/home/someone/.local/bin/claude')
        command = self.runtime.service_command(self.request, self.runtime.remote, network=True, read_only=('workspace',))
        remote = str(self.runtime.remote)
        self.assertIn(['--ro-bind', remote + '/workspace', remote + '/workspace'],
                      [command[i:i + 3] for i in range(len(command))])
        self.assertIn(['--ro-bind', '/home/someone/.local/bin', '/home/someone/.local/bin'],
                      [command[i:i + 3] for i in range(len(command))])

    def test_setup_job_launcher_starts_a_sealed_one_shot_sandbox_without_network(self):
        job = self.runtime.remote / 'jobs' / self.request.name
        command = self.runtime.service_command(self.request, job)
        self.assertEqual(command[:3], ['/usr/bin/python3', '-I', '-c'])
        self.assertEqual(command[4:7], [str(self.root / 'control/units' / (self.request.name + '.json')),
                                        str(self.run / 'artifacts' / (self.request.name + '.log')), '--'])
        chain = command[7:]
        expected = ['/usr/bin/unshare', '--user', *self.map, '--mount', '--mount-proc', '--pid', '--fork', '--kill-child',
                    '--', '/usr/bin/setpriv', '--reuid', '1000', '--regid', '1000', '--clear-groups', '--bounding-set',
                    '-all', '--inh-caps', '-all', '--no-new-privs', '--', '/usr/bin/bwrap']
        self.assertEqual(chain[:len(expected)], expected)
        self.assertIn('--no-new-privs', chain)
        self.assertNotIn('--net', chain)
        self.assertIn(str(job / 'resolv.conf'), chain)
        self.assertEqual(chain[-1], str(job))

    def test_service_command_refuses_a_foreign_binding(self):
        foreign = msgspec.structs.replace(self.request, spec=c.RuntimeSpec())
        with self.assertRaisesRegex(c.ContractError, 'invalid runtime binding'):
            self.runtime.service_command(foreign, self.runtime.remote)

    def test_nft_rules_accept_the_resolver_first_and_deny_every_host_address(self):
        addresses = [{'addr_info': [{'local': '127.0.0.1'}, {'local': '::1'}]},
                     {'addr_info': [{'local': '192.168.1.145'}, {'local': '2001:db8::10'}, {'local': 'fe80::1'}]}]
        text = rootless.nft_rules(addresses)
        lines = [line.strip() for line in text.splitlines()]
        self.assertEqual(lines[3:5], ['ip daddr 10.0.2.3 accept', 'ip daddr 10.0.2.2 drop'])
        self.assertIn('ip daddr { 127.0.0.1, 192.168.1.145 } drop', lines)
        self.assertIn('ip6 daddr { 2001:db8::10, ::1, fe80::1 } drop', lines)
        for rule in ('ip daddr 127.0.0.0/8 drop', 'ip6 daddr ::1/128 drop', 'ip daddr 10.0.0.0/8 drop',
                     'ip daddr 172.16.0.0/12 drop', 'ip daddr 192.168.0.0/16 drop', 'ip6 daddr fe80::/10 drop',
                     'ip6 daddr fc00::/7 drop'):
            self.assertIn(rule, lines)
        self.assertNotIn('{  }', rootless.nft_rules([]))

    def test_inspect_refuses_missing_subordinate_ids_or_binaries(self):
        with patch.object(rootless, 'sub_base', side_effect=c.ContractError('no subordinate range')), \
             self.assertRaisesRegex(c.ContractError, 'subordinate'):
            self.runtime.inspect()
        with patch.object(rootless, 'sub_base', return_value=100001), self.assertRaisesRegex(c.ContractError, 'differ'):
            self.runtime.inspect()
        with patch.object(rootless, 'sub_base', return_value=100000), \
             patch.object(rootless, 'BINARIES', ('/nonexistent/unshare',)), \
             self.assertRaisesRegex(c.ContractError, 'requires /nonexistent/unshare'):
            self.runtime.inspect()

    def test_sub_base_reads_the_user_entry(self):
        with tempfile.NamedTemporaryFile('w') as table:
            table.write('other:200000:65536\ntester:100000:65536\n')
            table.flush()
            self.assertEqual(rootless.sub_base(table.name, 'tester'), 100000)
            with self.assertRaisesRegex(c.ContractError, 'no subordinate range for missing'):
                rootless.sub_base(table.name, 'missing')

    def test_state_and_cleanup_follow_a_recorded_process(self):
        name = 'bk-' + 'c' * 32
        self.assertEqual(self.runtime.state(name)['LoadState'], 'not-found')
        self.assertTrue(self.runtime.cleanup(name))
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.runtime.outer = process
        self.runtime.record_unit(name, process.pid)
        state = self.runtime.state(name)
        self.assertEqual((state['ActiveState'], state['MainPID']), ('active', str(process.pid)))
        self.assertTrue(self.runtime.cleanup(name))
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.runtime.state(name)['LoadState'], 'not-found')

    def test_recorded_pid_reused_by_another_process_is_not_alive(self):
        name = 'bk-' + 'd' * 32
        self.runtime.units().mkdir(mode=0o700)
        (self.runtime.units() / (name + '.json')).write_text(json.dumps({'pid': os.getpid(), 'start': '1'}))
        self.assertEqual(self.runtime.state(name)['ActiveState'], 'inactive')

    def test_confirm_stopped_looks_for_sub_uid_processes(self):
        self.assertTrue(self.runtime.confirm_stopped())
        mine = RootlessRuntime(spec(self.root, subuid_base=os.getuid()), self.run, generic('/bin/true'))
        self.assertFalse(mine.confirm_stopped())

    def test_verify_model_requires_a_binary_the_sandbox_can_run_but_not_change(self):
        identity = RootlessRuntime.verify_model('/usr/bin/python3', None)
        self.assertRegex(identity, '^sha256:')
        # /tmp itself must be traversable by other users; TMPDIR may be a private directory.
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            os.chmod(folder, 0o755)
            binary = Path(folder) / 'agent'
            binary.write_bytes(b'#!/bin/sh\n')
            for mode, error in ((0o700, 'readable and executable'), (0o777, 'not writable')):
                binary.chmod(mode)
                with self.subTest(mode=oct(mode)), self.assertRaisesRegex(c.ContractError, error):
                    RootlessRuntime.verify_model(str(binary), None)
            binary.chmod(0o755)
            os.chmod(folder, 0o750)
            with self.assertRaisesRegex(c.ContractError, 'cannot reach'):
                RootlessRuntime.verify_model(str(binary), None)
            os.chmod(folder, 0o755)
            with self.assertRaisesRegex(c.ContractError, 'frozen binary digest'):
                RootlessRuntime.verify_model(str(binary), 'sha256:' + '0' * 64)


if __name__ == '__main__':
    unittest.main()
