"""CLI parsing and run orchestration against a fake runtime; no host privilege or agent binary is used."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

import msgspec

from worker_sandbox import __main__ as cli, contracts as c, hostconfig
from fixtures import FakeRuntime

SETUP_HOST = Path(__file__).resolve().parents[1] / 'tools/setup_host.py'


def setup_host_module():
    spec = importlib.util.spec_from_file_location('setup_host_under_test', SETUP_HOST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParsingTests(unittest.TestCase):
    def parse(self, *argv):
        return cli.parse(list(argv))

    def usage_error(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
            self.parse(*argv)
        self.assertEqual(raised.exception.code, 2)
        return errors.getvalue()

    def test_setup_host(self):
        args = self.parse('setup-host', '--controller', 'owner')
        self.assertEqual((args.controller, args.account, args.worker_root, args.control_root, args.python),
                         ('owner', 'worker-sandbox', '/var/lib/worker-sandbox-worker',
                          '/var/lib/worker-sandbox-controller', '/usr/bin/python3'))
        args = self.parse('setup-host', '--controller', 'owner', '--account', 'other', '--worker-root', '/w',
                          '--control-root', '/c', '--python', '/opt/python')
        self.assertEqual((args.account, args.worker_root, args.control_root, args.python), ('other', '/w', '/c', '/opt/python'))
        self.usage_error('setup-host')

    def test_doctor(self):
        args = self.parse('doctor', '--report', 'r.json')
        self.assertEqual((args.report, args.profile, args.binary), (Path('r.json'), 'generic', None))
        args = self.parse('doctor', '--report', 'r.json', '--profile', 'claude', '--binary', '/opt/claude')
        self.assertEqual((args.profile, args.binary), ('claude', '/opt/claude'))
        self.usage_error('doctor')

    def test_login(self):
        self.assertEqual(self.parse('login', '--profile', 'claude').profile, 'claude')
        self.assertEqual(self.parse('login', '--profile', 'codex', '--binary', '/b').binary, '/b')
        self.usage_error('login', '--profile', 'generic')
        self.usage_error('login')

    def test_run(self):
        args = self.parse('run', '--profile', 'claude', '--workspace', 'w')
        self.assertEqual((args.out, args.model, args.effort, args.resume, args.home_dir, args.env, args.no_credentials,
                          args.binary, args.binary_digest, args.extra),
                         (Path('sandbox-runs'), None, None, None, None, [], False, None, None, []))
        args = self.parse('run', '--profile', 'claude', '--workspace', 'w', '--out', 'o', '--model', 'm', '--effort', 'high',
                          '--resume', 's', '--home-dir', 'h', '--env', 'ANTHROPIC_API_KEY', '--env', 'CLAUDE_CODE_OAUTH_TOKEN',
                          '--no-credentials', '--binary', '/b', '--binary-digest', 'sha256:' + 'a' * 64, '--', '--bare', '-x')
        self.assertEqual((args.out, args.model, args.effort, args.resume, args.home_dir, args.env, args.no_credentials,
                          args.binary, args.extra),
                         (Path('o'), 'm', 'high', 's', Path('h'), ['ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'], True,
                          '/b', ['--bare', '-x']))
        self.usage_error('run', '--profile', 'claude')
        self.usage_error('run', '--workspace', 'w')

    def test_env_outside_the_profile_allowlist_is_a_usage_error(self):
        self.assertIn('not a credential variable', self.usage_error(
            'run', '--profile', 'claude', '--workspace', 'w', '--env', 'OPENAI_API_KEY'))
        self.usage_error('run', '--profile', 'codex', '--workspace', 'w', '--env', 'HOME')
        self.usage_error('run', '--profile', 'generic', '--workspace', 'w', '--env', 'OPENAI_API_KEY')
        self.assertEqual(self.parse('run', '--profile', 'codex', '--workspace', 'w', '--env', 'OPENAI_API_KEY').env,
                         ['OPENAI_API_KEY'])

    def test_recover(self):
        self.assertEqual(self.parse('recover', '/runs/' + 'a' * 32).run, Path('/runs/' + 'a' * 32))
        self.usage_error('recover')

    def test_there_is_no_timeout_option(self):
        self.usage_error('run', '--profile', 'claude', '--workspace', 'w', '--timeout', '5')


class RunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'input'
        self.workspace.mkdir()
        (self.workspace / 'README.md').write_text('fixture\n')
        self.out = self.root / 'out'
        self.spec = c.RuntimeSpec(control_root=str(self.root / 'control'))
        self.fake = FakeRuntime()
        self.built = []
        read = patch('worker_sandbox.__main__.hostconfig.read', return_value=self.spec)
        read.start()
        self.addCleanup(read.stop)

    def build(self, spec, run, profile, *, authentication, home_dir):
        self.built.append((spec, run, profile, authentication, home_dir))
        self.fake.root = run
        return self.fake

    def args(self, *extra):
        return cli.parse(['run', '--profile', 'codex', '--workspace', str(self.workspace), '--out', str(self.out),
                          '--binary', '/usr/bin/native', *extra])

    def run_cli(self, *extra):
        return cli.run(self.args(*extra), b'prompt', build=self.build, verify=self.fake.verify_model)

    def test_run_records_the_result_and_session(self):
        with patch.object(self.fake, 'start_run', wraps=self.fake.start_run) as start, \
             patch.object(self.fake, 'release', wraps=self.fake.release) as release:
            document = self.run_cli('--model', 'gpt-6-sol')
        start.assert_called_once()
        release.assert_called_once()
        run = self.out / 'runs' / document['run_id']
        self.assertRegex(document['run_id'], '^[0-9a-f]{32}$')
        self.assertEqual((document['profile'], document['session_id']), ('codex', 'session'))
        self.assertEqual((document['result'].outcome, document['result'].exit_code), ('completed', 0))
        self.assertEqual(json.loads((run / 'result.json').read_bytes()), json.loads(c.dumps(document)))
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o700)
        self.assertEqual({path.name for path in run.iterdir()}, {'artifacts', 'raw', 'workspace', 'result.json'})
        self.assertEqual((run / 'workspace/README.md').read_text(), 'fixture\n')
        self.assertTrue((run / 'workspace/.git').is_dir())
        status, agent = self.fake.invocations
        self.assertEqual(status.argv, ('/usr/bin/native', 'login', 'status'))
        self.assertEqual(agent.argv[-5:], ('exec', '--json', '--model', 'gpt-6-sol', '-'))
        self.assertEqual(agent.stdin, b'prompt')
        self.assertEqual(agent.home, str(run / 'home'))

    def test_preflight_failure_ends_with_provider_error_before_start_run(self):
        invoke = self.fake.invoke

        def failing_status(request, cancel=None):
            result = invoke(request, cancel)
            return msgspec.structs.replace(result, outcome='provider_error', exit_code=1)

        with patch.object(self.fake, 'invoke', side_effect=failing_status), \
             patch.object(self.fake, 'start_run') as start, \
             patch.object(self.fake, 'stop_run', return_value=True) as stop, \
             patch.object(self.fake, 'release') as release:
            document = self.run_cli()
        start.assert_not_called()
        stop.assert_called_once()
        release.assert_called_once()
        self.assertEqual(document['result'].outcome, 'provider_error')
        self.assertIsNone(document['session_id'])
        self.assertFalse(any('exec' in request.argv for request in self.fake.invocations))
        self.assertEqual(len(self.fake.invocations), 1)

    def test_main_exit_codes_follow_the_outcome(self):
        result = c.make(c.RuntimeResult, outcome='completed', exit_code=0, wall_sec=0, stdout_path=None,
                        stderr_path=None, cleanup='pending')
        argv = ['run', '--profile', 'generic', '--workspace', 'w']
        for outcome, code, expected in (('completed', 0, 0), ('completed', 3, 1), ('provider_error', 0, 1),
                                        ('cancelled', None, 130)):
            with self.subTest(outcome=outcome, code=code), contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO())), \
                 patch('worker_sandbox.__main__.run', return_value={'result': msgspec.structs.replace(
                     result, outcome=outcome, exit_code=code)}), patch('sys.stdin', io.TextIOWrapper(io.BytesIO())):
                self.assertEqual(cli.main(argv), expected)

    def test_out_directory_with_group_or_other_bits_is_refused(self):
        for mode in (0o750, 0o705):
            with self.subTest(mode=oct(mode)):
                self.out.mkdir(mode=0o700, exist_ok=True)
                self.out.chmod(mode)
                with self.assertRaisesRegex(c.ContractError, 'private directory'):
                    self.run_cli()
                self.assertFalse((self.out / 'runs').exists())
                self.assertEqual(self.built, [])

    def test_only_named_credential_variables_reach_the_runtime(self):
        self.run_cli()
        self.assertEqual(self.built[-1][2].credential_env, ())
        self.run_cli('--env', 'OPENAI_API_KEY', '--no-credentials', '--home-dir', 'relative-home')
        spec, run, profile, authentication, home_dir = self.built[-1]
        self.assertEqual(profile.credential_env, ('OPENAI_API_KEY',))
        self.assertFalse(authentication)
        self.assertEqual(home_dir, Path('relative-home').absolute())

    def test_extra_argv_is_appended_once(self):
        self.run_cli('--', '--flag')
        agent = self.fake.invocations[-1]
        self.assertEqual(agent.argv.count('--flag'), 1)
        self.assertEqual(agent.argv[-2:], ('-', '--flag'))


class RecoverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name) / 'control'
        self.control.mkdir(mode=0o700)
        self.run = Path(temporary.name) / ('a' * 32)
        spec = c.RuntimeSpec(control_root=str(self.control))
        read = patch('worker_sandbox.__main__.hostconfig.read', return_value=spec)
        read.start()
        self.addCleanup(read.stop)

    def test_recover_refuses_a_run_whose_lease_belongs_to_another_run(self):
        (self.control / 'lease.json').write_bytes(c.dumps({'run': str(self.run.parent / ('b' * 32))}))
        built = []
        with self.assertRaisesRegex(c.ContractError, 'leased by another run'):
            cli.recover(self.run, build=lambda *args, **kwargs: built.append(args))
        self.assertEqual(built, [])
        self.assertTrue((self.control / 'lease.json').exists())

    def test_recover_stops_confirms_and_releases_this_run(self):
        (self.control / 'lease.json').write_bytes(c.dumps({'run': str(self.run)}))
        fake = FakeRuntime()
        calls = []
        for name in ('stop_run', 'confirm_stopped', 'release'):
            setattr(fake, name, (lambda name, original: lambda: calls.append(name) or original())(name, getattr(fake, name)))
        result = cli.recover(self.run, build=lambda spec, run, profile, *, authentication: fake)
        self.assertEqual(calls, ['stop_run', 'confirm_stopped', 'release'])
        self.assertEqual((result['units_stopped'], result['account_quiet']), (True, True))


class RootlessSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name) / 'state/worker-sandbox/controller'
        self.spec = c.validate({'account': 'tester', 'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000,
                                'control_root': str(self.control), 'worker_root': str(Path(temporary.name) / 'w/worker')},
                               c.RuntimeSpec)
        default = patch.object(cli.rootless, 'default_control_root', return_value=self.control)
        default.start()
        self.addCleanup(default.stop)

    def test_setup_rootless_parses_without_arguments(self):
        self.assertEqual(cli.parse(['setup-rootless']).command, 'setup-rootless')

    def test_runtime_class_follows_the_spec_mode(self):
        self.assertIs(cli.runtime_class(c.RuntimeSpec()), cli.NativeRuntime)
        self.assertIs(cli.runtime_class(self.spec), cli.rootless.RootlessRuntime)

    def test_host_spec_prefers_this_users_rootless_config(self):
        root_spec = c.RuntimeSpec()
        with patch.object(cli.hostconfig, 'read', return_value=root_spec) as read, patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WORKER_SANDBOX_MODE', None)
            self.assertIs(cli.host_spec(), root_spec)
            read.assert_called_once_with()
        self.control.mkdir(parents=True, mode=0o700)
        hostconfig.write(self.spec)
        os.environ.pop('WORKER_SANDBOX_MODE', None)
        self.assertEqual(cli.host_spec(), self.spec)
        with patch.object(cli.hostconfig, 'read', return_value=root_spec), \
             patch.dict(os.environ, {'WORKER_SANDBOX_MODE': 'root'}):
            self.assertIs(cli.host_spec(), root_spec)

    def test_setup_rootless_refuses_an_existing_host_config(self):
        self.control.mkdir(parents=True, mode=0o700)
        hostconfig.write(self.spec)
        Path(self.spec.worker_root).mkdir(parents=True)
        with patch.object(cli.rootless, 'default_spec', return_value=self.spec), \
             patch.object(cli.rootless, 'provision') as provision, \
             patch.object(cli.rootless, 'provision_worker') as worker, \
             self.assertRaisesRegex(c.ContractError, 'exists'):
            cli.setup_rootless(cli.parse(['setup-rootless']))
        provision.assert_not_called()
        worker.assert_not_called()

    def test_setup_rootless_recreates_only_a_removed_worker_root(self):
        self.control.mkdir(parents=True, mode=0o700)
        hostconfig.write(self.spec)
        with patch.object(cli.rootless, 'default_spec', return_value=self.spec), \
             patch.object(cli.rootless, 'check_host', return_value={'apparmor_restricted': True}), \
             patch.object(cli.rootless, 'provision_worker', return_value={'worker_root': self.spec.worker_root,
                                                                         'worker_owner': 100000}) as worker, \
             patch.object(cli.rootless, 'provision') as provision:
            result = cli.setup_rootless(cli.parse(['setup-rootless']))
        worker.assert_called_once_with(self.spec)
        provision.assert_not_called()
        self.assertTrue(result['repaired'])

    def test_setup_rootless_provisions_then_records_the_spec(self):
        self.control.parent.mkdir(parents=True)
        def provision(spec):
            Path(spec.control_root).mkdir(mode=0o700)
            return {'control_root': spec.control_root, 'worker_root': spec.worker_root, 'worker_owner': 100000}
        with patch.object(cli.rootless, 'default_spec', return_value=self.spec), \
             patch.object(cli.rootless, 'check_host', return_value={'apparmor_restricted': True}), \
             patch.object(cli.rootless, 'provision', side_effect=provision):
            result = cli.setup_rootless(cli.parse(['setup-rootless']))
        self.assertEqual(hostconfig.read(str(self.control)), self.spec)
        self.assertEqual((result['worker_owner'], result['apparmor_restricted']), (100000, True))


class DoctorTests(unittest.TestCase):
    def test_doctor_imports_and_never_overwrites_a_report(self):
        spec = importlib.util.spec_from_file_location('doctor_under_test', SETUP_HOST.parent / 'doctor.py')
        doctor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doctor)
        with tempfile.NamedTemporaryFile() as existing, patch.object(doctor, 'NativeRuntime') as runtime, \
             self.assertRaises(FileExistsError):
            doctor.verify(Path(existing.name), 120, cli.profiles.claude('/opt/claude'), check_binary=True)
        runtime.assert_not_called()


class HostConfigTests(unittest.TestCase):
    def test_setup_host_writes_the_bytes_hostconfig_reads(self):
        setup_host = setup_host_module()
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / 'control'
            control.mkdir(mode=0o700)
            spec = c.RuntimeSpec(account='sandbox-a', worker_root='/var/lib/a', control_root=str(control),
                                 python='/opt/python3')
            # Root-mode host.json omits the stage 2 fields; they read back as their root-mode defaults.
            self.assertEqual(c.loads(setup_host.host_document('sandbox-a', Path('/var/lib/a'), control, '/opt/python3'),
                                     c.RuntimeSpec), spec)
            with patch.object(setup_host.pwd, 'getpwnam', return_value=pwd.getpwuid(os.getuid())), \
                 contextlib.redirect_stdout(io.StringIO()):
                target = setup_host.write_host('owner', 'sandbox-a', Path('/var/lib/a'), control, '/opt/python3')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(hostconfig.read(str(control)), spec)
            with self.assertRaises(FileExistsError):
                hostconfig.write(spec)
            target.unlink()
            self.assertEqual(hostconfig.write(spec).read_bytes(), c.dumps(spec))

    def test_host_config_must_describe_its_own_control_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            (control / 'host.json').write_bytes(c.dumps(c.RuntimeSpec(control_root='/elsewhere')))
            with self.assertRaisesRegex(c.ContractError, 'another control root'):
                hostconfig.read(str(control))
            with self.assertRaisesRegex(c.ContractError, 'setup-host'):
                hostconfig.read(str(control / 'missing'))


if __name__ == '__main__':
    unittest.main()
