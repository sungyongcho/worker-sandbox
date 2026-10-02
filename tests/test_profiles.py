"""Agent profile argv, HOME layout and session parsing; no agent binary is executed."""
import json
import unittest

import msgspec

from worker_sandbox import contracts as c
from worker_sandbox.profiles import AgentProfile, claude, codex, generic

BINARY = '/usr/local/lib/agent/bin/agent'
XDG = {'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'}


def lines(*events):
    return b'\n'.join(json.dumps(event).encode() for event in events)


class CommandTests(unittest.TestCase):
    def test_codex_command_without_options(self):
        self.assertEqual(codex(BINARY).command(model=None, effort=None, resume=None, extra=()), (
            BINARY, '-c', 'approval_policy="never"', '-c', 'default_permissions="worker_sandbox"',
            '-c', 'permissions.worker_sandbox.extends=":workspace"',
            '-c', 'permissions.worker_sandbox.network.enabled=true', 'exec', '--json', '-'))

    def test_codex_command_with_every_option(self):
        argv = codex(BINARY).command(model='gpt-6-sol', effort='medium', resume='thread-1', extra=('--flag', 'x'))
        self.assertEqual(argv, (
            BINARY, '-c', 'model_reasoning_effort="medium"', '-c', 'approval_policy="never"',
            '-c', 'default_permissions="worker_sandbox"', '-c', 'permissions.worker_sandbox.extends=":workspace"',
            '-c', 'permissions.worker_sandbox.network.enabled=true', 'exec', '--json', '--model', 'gpt-6-sol',
            'resume', 'thread-1', '-', '--flag', 'x'))

    def test_codex_options_are_independent(self):
        profile = codex(BINARY)
        self.assertEqual(profile.command(model='m', effort=None, resume=None, extra=())[-4:],
                         ('--json', '--model', 'm', '-'))
        self.assertEqual(profile.command(model=None, effort='high', resume=None, extra=())[1:3],
                         ('-c', 'model_reasoning_effort="high"'))
        self.assertEqual(profile.command(model=None, effort=None, resume='s', extra=())[-4:],
                         ('--json', 'resume', 's', '-'))

    def test_claude_command_without_options(self):
        self.assertEqual(claude(BINARY).command(model=None, effort=None, resume=None, extra=()), (
            BINARY, '-p', '--output-format', 'stream-json', '--verbose',
            '--dangerously-skip-permissions', '--permission-prompts', 'none', '--strict-mcp-config'))

    def test_claude_command_with_every_option(self):
        argv = claude(BINARY).command(model='opus', effort='high', resume='session-1', extra=('--bare',))
        self.assertEqual(argv, (
            BINARY, '-p', '--output-format', 'stream-json', '--verbose', '--dangerously-skip-permissions',
            '--permission-prompts', 'none', '--strict-mcp-config', '--model', 'opus', '--effort', 'high', '--resume',
            'session-1', '--bare'))
        self.assertNotIn('--bare', claude(BINARY).command(model=None, effort=None, resume=None, extra=()))

    def test_claude_options_are_independent(self):
        profile = claude(BINARY)
        self.assertEqual(profile.command(model='m', effort=None, resume=None, extra=())[-2:], ('--model', 'm'))
        self.assertEqual(profile.command(model=None, effort='low', resume=None, extra=())[-2:], ('--effort', 'low'))
        self.assertEqual(profile.command(model=None, effort=None, resume='s', extra=())[-2:], ('--resume', 's'))

    def test_generic_command_is_the_binary_and_extra_only(self):
        profile = generic(BINARY)
        self.assertEqual(profile.command(model=None, effort=None, resume=None, extra=()), (BINARY,))
        self.assertEqual(profile.command(model='m', effort='e', resume='s', extra=('-c', 'echo')), (BINARY, '-c', 'echo'))


class HomeEnvironmentTests(unittest.TestCase):
    def test_home_environment_keys(self):
        for profile, variable in ((codex(BINARY), 'CODEX_HOME'), (claude(BINARY), 'CLAUDE_CONFIG_DIR'),
                                  (generic(BINARY), None)):
            with self.subTest(profile=profile.name):
                environment = profile.home_environment('/w/home')
                expected = {'HOME'} | XDG | ({variable} if variable else set())
                self.assertEqual(set(environment), expected)
                self.assertEqual(environment['HOME'], '/w/home')
                self.assertEqual(environment['XDG_CONFIG_HOME'], '/w/home/.config')
                self.assertEqual(environment['XDG_DATA_HOME'], '/w/home/.local/share')
                self.assertEqual(environment['XDG_CACHE_HOME'], '/w/home/.cache')
                self.assertEqual(environment['XDG_STATE_HOME'], '/w/home/.local/state')

    def test_config_directory_is_inside_home(self):
        self.assertEqual(codex(BINARY).home_environment('/w/home')['CODEX_HOME'], '/w/home/.codex')
        self.assertEqual(claude(BINARY).home_environment('/w/home')['CLAUDE_CONFIG_DIR'], '/w/home/.claude')

    def test_credential_files_live_under_the_config_directory(self):
        # Measured with claude 2.1.286 and codex 0.157.0 logins: every staged file sits in the config directory.
        self.assertEqual(codex(BINARY).credential_files, ('.codex/auth.json',))
        self.assertEqual(claude(BINARY).credential_files, ('.claude/.credentials.json', '.claude/.claude.json'))
        self.assertEqual(generic(BINARY).credential_files, ())

    def test_profiles_validate_as_contracts(self):
        for profile in (codex(BINARY), claude(BINARY), generic(BINARY)):
            with self.subTest(profile=profile.name):
                self.assertEqual(c.validate(profile, AgentProfile), profile)
        with self.assertRaises(c.ContractError):
            c.validate({**msgspec.to_builtins(generic(BINARY)), 'name': 'other'}, AgentProfile)


class SessionTests(unittest.TestCase):
    def test_codex_thread_started(self):
        stdout = lines({'type': 'thread.started', 'thread_id': 'thread-1'}, {'type': 'turn.started'},
                       {'type': 'thread.started', 'thread_id': 'thread-2'})
        self.assertEqual(codex(BINARY).session_id(stdout), 'thread-1')

    def test_claude_result_line(self):
        stdout = lines({'type': 'system', 'subtype': 'init', 'session_id': 'from-init'},
                       {'type': 'assistant', 'session_id': 'from-message'},
                       {'type': 'result', 'subtype': 'success', 'session_id': 'from-result'})
        self.assertEqual(claude(BINARY).session_id(stdout), 'from-result')

    def test_claude_system_init_line_without_result(self):
        stdout = lines({'type': 'system', 'subtype': 'hook', 'session_id': 'from-hook'},
                       {'type': 'system', 'subtype': 'init', 'session_id': 'from-init'},
                       {'type': 'assistant', 'session_id': 'from-message'})
        self.assertEqual(claude(BINARY).session_id(stdout), 'from-init')

    def test_garbage_returns_none(self):
        garbage = (b'not json\n\xff\xfe\n[1, 2]\n"text"\n{"type": "result", "session_id": 7}\n'
                   b'{"type": "thread.started", "thread_id": null}\n' + b'[' * 100000)
        for profile in (codex(BINARY), claude(BINARY), generic(BINARY)):
            with self.subTest(profile=profile.name):
                self.assertIsNone(profile.session_id(garbage))

    def test_empty_bytes_return_none(self):
        for profile in (codex(BINARY), claude(BINARY), generic(BINARY)):
            with self.subTest(profile=profile.name):
                self.assertIsNone(profile.session_id(b''))

    def test_generic_never_parses(self):
        self.assertIsNone(generic(BINARY).session_id(lines({'type': 'result', 'session_id': 'x'})))


if __name__ == '__main__':
    unittest.main()
