"""Agent CLI profiles: argv, HOME layout, credential files and session parsing per agent."""
from __future__ import annotations

import json
from typing import Literal

from .contracts import Model


class AgentProfile(Model):
    name: Literal['codex', 'claude', 'generic']
    # Absolute path of the agent executable; NativeRuntime.verify_model checks it is root-owned.
    binary: str
    # The variable that relocates the agent's state directory into HOME, and that directory.
    config_env: str | None
    config_subdir: str
    # Paths relative to HOME, copied from <control_root>/credentials/<name>/ at claim.
    credential_files: tuple[str, ...]
    # The only controller environment variables that may cross into a job.
    credential_env: tuple[str, ...]
    environment: dict[str, str]
    login_argv: tuple[str, ...]
    status_argv: tuple[str, ...]
    # Hosts the doctor probes from inside the sandbox.
    hosts: tuple[str, ...]
    # Recorded for stage 2; unused in stage 1.
    evidence_directories: tuple[str, ...]

    def home_environment(self, home: str) -> dict[str, str]:
        """Point every agent state location into one private HOME."""
        environment = {'HOME': home}
        if self.config_env is not None:
            environment[self.config_env] = home + '/' + self.config_subdir
        environment.update({'XDG_CONFIG_HOME': home + '/.config', 'XDG_DATA_HOME': home + '/.local/share',
                            'XDG_CACHE_HOME': home + '/.cache', 'XDG_STATE_HOME': home + '/.local/state'})
        return environment

    def command(self, *, model: str | None, effort: str | None, resume: str | None,
                extra: tuple[str, ...]) -> tuple[str, ...]:
        """Return argv for one invocation; the prompt is always read from stdin."""
        argv = [self.binary]
        if self.name == 'codex':
            if effort is not None:
                argv += ['-c', f'model_reasoning_effort="{effort}"']
            argv += ['-c', 'approval_policy="never"', '-c', 'default_permissions="worker_sandbox"',
                     '-c', 'permissions.worker_sandbox.extends=":workspace"',
                     '-c', 'permissions.worker_sandbox.network.enabled=true']
            argv += ['exec', '--json']
            if model is not None:
                argv += ['--model', model]
            if resume is not None:
                argv += ['resume', resume]
            argv += ['-']
        elif self.name == 'claude':
            argv += ['-p', '--output-format', 'stream-json', '--verbose',
                     '--dangerously-skip-permissions', '--permission-prompts', 'none']
            if model is not None:
                argv += ['--model', model]
            if effort is not None:
                argv += ['--effort', effort]
            if resume is not None:
                argv += ['--resume', resume]
        return tuple(argv) + tuple(extra)

    def session_id(self, stdout: bytes) -> str | None:
        """Find the session identifier in the agent's JSON event stream; never raises."""
        found = None
        if self.name == 'generic':
            return found
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(event, dict):
                continue
            if self.name == 'codex':
                if event.get('type') == 'thread.started':
                    value = event.get('thread_id')
                    return value if isinstance(value, str) and value else None
                continue
            value = event.get('session_id')
            if not isinstance(value, str) or not value:
                continue
            if event.get('type') == 'result':
                return value
            if found is None and event.get('type') == 'system' and event.get('subtype') == 'init':
                found = value
        return found


def codex(binary: str) -> AgentProfile:
    return AgentProfile(name='codex', binary=binary, config_env='CODEX_HOME', config_subdir='.codex',
                        credential_files=('.codex/auth.json',), credential_env=('OPENAI_API_KEY',),
                        environment={}, login_argv=(binary, 'login', '--device-auth'),
                        status_argv=(binary, 'login', 'status'),
                        hosts=('chatgpt.com', 'auth.openai.com', 'api.openai.com'),
                        evidence_directories=('.codex/sessions', '.codex/log'))


def claude(binary: str) -> AgentProfile:
    return AgentProfile(name='claude', binary=binary, config_env='CLAUDE_CONFIG_DIR', config_subdir='.claude',
                        credential_files=('.claude/.credentials.json', '.claude.json'),
                        credential_env=('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'),
                        environment={'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
                                     'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'},
                        login_argv=(binary, 'auth', 'login'), status_argv=(binary, 'auth', 'status', '--json'),
                        hosts=('api.anthropic.com', 'claude.ai'), evidence_directories=('.claude/projects',))


def generic(binary: str) -> AgentProfile:
    return AgentProfile(name='generic', binary=binary, config_env=None, config_subdir='',
                        credential_files=(), credential_env=(), environment={}, login_argv=(), status_argv=(),
                        hosts=('api.openai.com', 'api.anthropic.com'), evidence_directories=())
