"""Small explicit CLI: run one agent CLI invocation inside the account-isolated sandbox."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import uuid

import msgspec

from . import contracts as c, hostconfig, profiles
from .artifacts import _manifest, atomic_write, safe_read, tree_manifest
from .ownership import exclusive
from .runtime import NativeRuntime, control_environment
from .seed import seed_repository

PROFILES = {'codex': profiles.codex, 'claude': profiles.claude, 'generic': profiles.generic}
# Diagnostic observer timeout passed to the doctor; not a limit on agent execution.
DOCTOR_TIMEOUT = 120


def _tool(name: str):
    """tools/ is not packaged; setup-host and doctor load it from the source checkout beside the package."""
    path = Path(__file__).resolve().parents[1] / 'tools' / (name + '.py')
    if not path.is_file():
        raise c.ContractError(f'{name} runs from the source checkout; {path} is missing')
    spec = importlib.util.spec_from_file_location('worker_sandbox_tools_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent(name: str, binary: str | None) -> profiles.AgentProfile:
    """The selected profile; without --binary, the agent is looked up on the worker's PATH."""
    if binary is None:
        binary = shutil.which(name, path=control_environment()['PATH'])
        if binary is None:
            raise c.ContractError(f'no {name} executable on {control_environment()["PATH"]}; pass --binary')
    return PROFILES[name](str(Path(binary).absolute()))


def setup_host(args) -> dict:
    module = _tool('setup_host')
    module.prepare(args.controller, args.account, Path(args.worker_root), Path(args.control_root))
    target = module.write_host(args.controller, args.account, Path(args.worker_root), Path(args.control_root), args.python)
    return {'host': str(target), 'spec': hostconfig.read(args.control_root)}


def doctor(args) -> dict:
    """The live host checks; the agent binary check runs only when --binary is given."""
    profile = PROFILES[args.profile](str(Path(args.binary).absolute()) if args.binary else '')
    return _tool('doctor').verify(args.report, DOCTOR_TIMEOUT, profile, check_binary=args.binary is not None)


def login(profile: profiles.AgentProfile) -> dict:
    """The agent's own login in a private staging HOME; its files are never opened here."""
    control = Path(hostconfig.read().control_root)
    with exclusive(control / 'owner.lock'):
        if (control / 'lease.json').exists():
            raise c.ContractError('login runs outside any run; finish or recover the leased run first')
        NativeRuntime.verify_model(profile.binary, None)
        staging = control / 'credentials' / profile.name
        # As native_login did: the agent's state directory exists before the agent starts.
        for path in (staging.parent, staging, *((staging / profile.config_subdir,) if profile.config_subdir else ())):
            path.mkdir(mode=0o700, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise c.ContractError(f'private owned directory required: {path}')
        environment = {**control_environment(), **profile.home_environment(str(staging)), **profile.environment}
        # The terminal is inherited; codes and credentials never pass through this process.
        completed = subprocess.run(profile.login_argv, cwd=staging, env=environment, check=False)
        if completed.returncode:
            raise c.ContractError(f'{profile.name} login failed ({completed.returncode})')
        status = subprocess.run(profile.status_argv, cwd=staging, env=environment, stdout=subprocess.PIPE, check=False)
    return {'profile': profile.name, 'status_exit_code': status.returncode,
            'status': status.stdout.decode(errors='replace')}


def output_root(path: Path) -> Path:
    root = Path(path).absolute()
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise c.ContractError(f'--out must be a private directory owned by this user: {root}')
    return root


def create_run(root: Path) -> Path:
    (root / 'runs').mkdir(mode=0o700, exist_ok=True)
    run = root / 'runs' / uuid.uuid4().hex
    run.mkdir(mode=0o700)
    for name in ('artifacts', 'raw', 'workspace'):
        (run / name).mkdir(mode=0o700)
    return run


def copy_workspace(source: Path, run: Path):
    """The kit's snapshot copy (no .git, modes 0400/0500), then one fresh seed commit."""
    _manifest(Path(source).absolute(), destination=run / 'workspace', exclude_generated=False)
    seed_repository(run / 'workspace')


def preflight(runtime, spec, run: Path, profile: profiles.AgentProfile):
    """Login status as a setup job before the run service starts; None when the agent is ready."""
    if not profile.status_argv:
        return None
    name = 'bk-' + uuid.uuid4().hex
    result = runtime.invoke(c.make(c.RuntimeRequest, spec=spec, name=name, workspace=str(run / 'workspace'),
                                   home=str(run / 'home'), argv=profile.status_argv, log_dir=str(run / 'raw' / name)))
    if result.outcome == 'completed' and result.exit_code == 0:
        return None
    return msgspec.structs.replace(result, outcome='provider_error',
                                   error=result.error or f'login status exited with {result.exit_code}')


def execute(runtime, spec, run: Path, profile: profiles.AgentProfile, args, prompt: bytes):
    """Steps 5 to 8: preflight, start the run service, invoke the agent once, read its session."""
    failed = preflight(runtime, spec, run, profile)
    if failed is not None:
        return failed, None
    runtime.start_run()
    name = 'bk-' + uuid.uuid4().hex
    argv = profile.command(model=args.model, effort=args.effort, resume=args.resume, extra=tuple(args.extra))
    request = c.make(c.RuntimeRequest, spec=spec, name=name, workspace=str(run / 'workspace'),
                     home=str(run / 'home'), argv=argv, log_dir=str(run / 'raw' / name), stdin=prompt)
    cancelled = []
    previous = signal.signal(signal.SIGINT, lambda *_: cancelled.append(True))
    try:
        result = runtime.invoke(request, cancel=lambda: bool(cancelled))
    finally:
        signal.signal(signal.SIGINT, previous)
    stdout = Path(result.stdout_path).read_bytes() if result.stdout_path else b''
    return result, profile.session_id(stdout)


def run(args, prompt: bytes, *, build=NativeRuntime, verify=NativeRuntime.verify_model) -> dict:
    spec = hostconfig.read()
    profile = agent(args.profile, args.binary)
    # Only the variables named with --env may cross; the profile lists which names are allowed.
    profile = msgspec.structs.replace(profile, credential_env=tuple(args.env))
    verify(profile.binary, args.binary_digest)
    run = create_run(output_root(args.out))
    copy_workspace(args.workspace, run)
    runtime = build(spec, run, profile, authentication=not args.no_credentials,
                    home_dir=args.home_dir.absolute() if args.home_dir is not None else None)
    runtime.claim()
    runtime.verify_seed(tree_manifest(run / 'workspace', exclude_generated=False))
    try:
        result, session = execute(runtime, spec, run, profile, args, prompt)
    finally:
        runtime.stop_run()
    runtime.collect_workspace()
    document = {'run_id': run.name, 'profile': profile.name, 'session_id': session, 'result': result,
                'stdout': result.stdout_path, 'stderr': result.stderr_path}
    atomic_write(run / 'result.json', c.dumps(document))
    runtime.release()
    return document


def recover(run: Path, *, build=NativeRuntime) -> dict:
    """After a controller crash: stop this run's units, confirm no worker process, release its files."""
    spec = hostconfig.read()
    control = Path(spec.control_root)
    lease = control / 'lease.json'
    if lease.exists() and c.loads(safe_read(lease, control), dict) != {'run': str(Path(run).absolute())}:
        raise c.ContractError(f'{spec.account} is leased by another run; recover that run instead')
    runtime = build(spec, Path(run), profiles.generic('/bin/true'), authentication=False)
    stopped = runtime.stop_run()
    quiet = runtime.confirm_stopped()
    runtime.release()
    return {'run': str(Path(run).absolute()), 'units_stopped': stopped, 'account_quiet': quiet,
            'released': not lease.exists()}


def parser() -> argparse.ArgumentParser:
    defaults = c.RuntimeSpec()
    root = argparse.ArgumentParser(prog='worker-sandbox', description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup-host', help='Create the worker account and roots; run through sudo')
    setup.add_argument('--controller', required=True)
    setup.add_argument('--account', default=defaults.account)
    setup.add_argument('--worker-root', default=defaults.worker_root)
    setup.add_argument('--control-root', default=defaults.control_root)
    setup.add_argument('--python', default=defaults.python)
    doctor = commands.add_parser('doctor', help='Verify the host isolation live; needs sudo -v first')
    doctor.add_argument('--report', type=Path, required=True)
    doctor.add_argument('--profile', choices=sorted(PROFILES), default='generic')
    doctor.add_argument('--binary')
    signin = commands.add_parser('login', help="Run the agent's own login into the private staging HOME")
    signin.add_argument('--profile', choices=('claude', 'codex'), required=True)
    signin.add_argument('--binary')
    execution = commands.add_parser('run', help='Run one agent invocation; the prompt is read from stdin')
    execution.add_argument('--profile', choices=sorted(PROFILES), required=True)
    execution.add_argument('--workspace', type=Path, required=True)
    execution.add_argument('--out', type=Path, default=Path('sandbox-runs'))
    execution.add_argument('--model')
    execution.add_argument('--effort')
    execution.add_argument('--resume')
    execution.add_argument('--home-dir', type=Path)
    execution.add_argument('--env', action='append', default=[], metavar='NAME')
    execution.add_argument('--no-credentials', action='store_true')
    execution.add_argument('--binary')
    execution.add_argument('--binary-digest')
    execution.add_argument('extra', nargs='*', help='extra agent argv after --')
    recovery = commands.add_parser('recover', help='Stop and release a run left by a crashed controller')
    recovery.add_argument('run', type=Path)
    return root


def parse(argv=None):
    root = parser()
    args = root.parse_args(argv)
    if args.command == 'run':
        allowed = PROFILES[args.profile]('/').credential_env
        for name in args.env:
            if name not in allowed:
                root.error(f'--env {name} is not a credential variable of the {args.profile} profile: {", ".join(allowed) or "none"}')
    return args


def dispatch(args):
    handlers = {
        'setup-host': lambda: setup_host(args),
        'doctor': lambda: doctor(args),
        'login': lambda: login(agent(args.profile, args.binary)),
        'run': lambda: run(args, sys.stdin.buffer.read()),
        'recover': lambda: recover(args.run),
    }
    return handlers[args.command]()


def main(argv=None) -> int:
    args = parse(argv)
    try:
        result = dispatch(args)
        sys.stdout.buffer.write(c.dumps(result) + b'\n')
        if args.command == 'run':
            outcome = result['result'].outcome
            if outcome == 'cancelled':
                return 130
            return 0 if outcome == 'completed' and result['result'].exit_code == 0 else 1
        return 0
    except c.Halt as exc:
        print(f'worker-sandbox: {exc}', file=sys.stderr)
        return 130 if exc.outcome == 'cancelled' else 1
    except (c.ContractError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'worker-sandbox: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
