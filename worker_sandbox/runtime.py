"""Native execution under one dedicated account, with PID 1 owning descendants."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import grp
import hashlib
import ipaddress
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import time

from . import worker_files, worker_job, worker_service, seed
from .artifacts import atomic_output, atomic_write, replace_directory, safe_read, tree_manifest
from .contracts import ArtifactRef, ContractError, Halt, Interval, RuntimeRequest, RuntimeResult, RuntimeSpec, digest, dumps, loads, make, validate
from .ownership import exclusive
from .profiles import AgentProfile

_UNIT = re.compile(r'bk-[0-9a-f]{32}')

# Translate slirp's native readiness byte into PID 1's service-start handshake.
NETWORK_BROKER = r'''import os,socket,subprocess,sys
reader,writer=os.pipe()
try:
 child=subprocess.Popen([sys.argv[1],'--ready-fd='+str(writer),*sys.argv[2:]],pass_fds=(writer,))
finally:
 os.close(writer)
try:
 ready=os.read(reader,1)
finally:
 os.close(reader)
if ready != b'1':
 raise RuntimeError('Network broker exited before reporting readiness')
address=os.environ['NOTIFY_SOCKET']
if address.startswith('@'): address='\0'+address[1:]
with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as notify:
 notify.sendto(b'READY=1',address)
raise SystemExit(child.wait())
'''


def control_environment() -> dict[str, str]:
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}


def external_nameservers():
    """Use the host's configured upstream resolvers without changing host DNS."""
    path = Path('/run/systemd/resolve/resolv.conf')
    if not path.is_file():
        path = Path('/etc/resolv.conf')
    addresses = [line.split()[1] for line in path.read_text().splitlines() if line.startswith('nameserver ')]
    usable = [address for address in addresses if not ipaddress.ip_address(address).is_loopback
              and not ipaddress.ip_address(address).is_unspecified]
    if not usable:
        raise ContractError('Run internet requires an existing non-loopback upstream resolver; no DNS substitute is chosen')
    return usable


def checked_command(argv: list[str], *, data=None, timeout=30) -> bytes:
    completed = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=control_environment(), timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f'{Path(argv[0]).name} failed ({completed.returncode}): '
                           + completed.stderr.decode(errors='replace')[:1000])
    return completed.stdout


class NativeRuntime:
    """Owns the worker filesystem and systemd units; never reads or writes run DBs."""
    def __init__(self, spec: RuntimeSpec, run: Path, agent: AgentProfile, *, authentication=True, home_dir: Path | None = None,
                 path_prefix: str | None = None):
        self.spec = validate(spec, RuntimeSpec)
        self.run = Path(run).absolute()
        if not re.fullmatch('[0-9a-f]{32}', self.run.name):
            raise ContractError('run directory must be a generated run ID')
        self.remote = Path(spec.worker_root) / self.run.name
        self.control = Path(spec.control_root)
        self.lease = self.control / 'lease.json'
        self.bind_units(self.run.name)
        self.starts = 0
        self.service_active = False
        self.authentication = authentication
        self.agent = agent
        self.home_dir = Path(home_dir) if home_dir is not None else None
        self.spec_path_prefix = path_prefix

    def bind_units(self, identity):
        self.run_unit = 'bk-' + identity
        self.network_unit = 'bk-' + hashlib.md5((identity + ':network').encode(), usedforsecurity=False).hexdigest()

    def bridge_command(self):
        script = Path(worker_files.__file__).read_text()
        return ['/usr/bin/sudo', '-n', '-H', '-u', self.spec.account,
                self.spec.python, '-I', '-c', script, str(self.remote)]

    def rpc(self, action, **fields):
        raw = checked_command(self.bridge_command(), data=dumps({'action': action, **fields}) + b'\n',
                              timeout=None if action in {'list', 'file_refs', 'native_evidence'} else 30)
        return loads(raw, object)

    @contextmanager
    def transfer(self, action, **fields):
        """Stream file bytes separately from the small control-message channel."""
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.bridge_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=errors, env=control_environment())
            try:
                process.stdin.write(dumps({'action': action, **fields}) + b'\n')
                process.stdin.flush()
                yield process
                process.stdin.close()
                process.stdout.close()
                code = process.wait()
                if code:
                    errors.seek(0)
                    raise RuntimeError(f'worker transfer failed ({code}): ' + errors.read(1000).decode(errors='replace'))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()

    def download(self, relative: str, destination: Path, *, optional=False, prefix_size=None):
        identity, size = hashlib.sha256(), 0
        with atomic_output(destination) as stream:
            with self.transfer('pull', path=relative, optional=optional, prefix_size=prefix_size) as process:
                process.stdin.close()
                while chunk := process.stdout.read(worker_files.CHUNK_BYTES):
                    stream.write(chunk)
                    identity.update(chunk)
                    size += len(chunk)
            reference = make(ArtifactRef, path=destination.relative_to(self.run).as_posix(),
                             digest='sha256:' + identity.hexdigest(), size=size)
        return reference

    def upload_tree(self, source: Path, relative: str, refs):
        entries = loads(dumps(refs), list)
        with self.transfer('push_tree', path=relative, refs=entries) as process:
            worker_files.write_archive(source, entries, process.stdin)
            process.stdin.close()
            if loads(process.stdout.read(32), bool) is not True:
                raise ContractError('worker did not confirm tree transfer')

    def download_tree(self, destination: Path, refs, *, relative=None):
        entries = loads(dumps(refs), list)
        fields = {'path': relative} if relative is not None else {}
        with self.transfer('pull_tree', refs=entries, **fields) as process:
            process.stdin.close()
            try:
                worker_files.read_archive(process.stdout, destination, entries)
            except ValueError as exc:
                raise ContractError(f'Invalid worker tree transfer: {exc}') from exc

    def inspect(self):
        try:
            account = pwd.getpwnam(self.spec.account)
        except KeyError as exc:
            raise ContractError(f"{self.spec.account} account is not provisioned") from exc
        if account.pw_uid in {0, os.getuid()} or account.pw_shell not in {'/usr/sbin/nologin', '/sbin/nologin'}:
            raise ContractError('worker requires a distinct, non-login account')
        if account.pw_gid != grp.getgrnam(self.spec.account).gr_gid or account.pw_gid == os.getgid():
            raise ContractError('worker requires its own primary group')
        if any(self.spec.account in group.gr_mem for group in grp.getgrall()):
            raise ContractError('worker must not have supplementary groups')
        for path, uid in ((self.control, os.getuid()), (Path(self.spec.worker_root), account.pw_uid),
                          (self.run.parent.parent, os.getuid())):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
                raise ContractError(f'private owned directory required: {path}')
        version = checked_command(['/usr/bin/systemctl', '--version']).decode().split()[1]
        if int(version) < 250:
            raise ContractError('systemd >=250 is required for cgroup lifetime tracking')
        return {'account': self.spec.account, 'uid': account.pw_uid, 'systemd': version,
                'runtime_digest': digest(self.spec)}

    @staticmethod
    def verify_model(binary: str, binary_digest: str | None):
        binary = Path(binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ContractError('native binary must be an executable file')
        for path in (binary, *binary.parents):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ContractError('native installation must be root-owned and not group/world writable')
        with binary.open('rb') as stream:
            identity = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
        if binary_digest is not None and identity != binary_digest:
            raise ContractError('native executable differs from frozen binary digest')
        return identity

    def claim(self):
        self.inspect()
        with exclusive(self.control / 'owner.lock'):
            if self.lease.exists():
                if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
                    raise ContractError(f'{self.spec.account} is leased by another run; finish or recover it first')
                self.rpc('check')
                return
            if not self.confirm_stopped():
                raise ContractError('worker account still has processes; recover before reusing it')
            if not self.rpc('empty'):
                raise ContractError('worker home is not empty; inspect and preserve existing state before claiming a run')
            atomic_write(self.lease, dumps({'run': str(self.run)}))
            self.rpc('init')
            self.populate()

    def populate(self):
        """Materialize the controller's pristine workspace as a new independent repository."""
        source = self.run / 'workspace'
        self.upload_tree(source, 'workspace', tree_manifest(source, exclude_generated=False))
        expected = checked_command(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
        self.rpc('seed', script=Path(seed.__file__).read_text(), expected=expected)
        if self.authentication:
            self.seed_native_state()
        self.rpc('check')

    def seed_native_state(self):
        """Seed the agent HOME with the staged login files and the optional home directory."""
        if self.home_dir is not None:
            for ref in tree_manifest(self.home_dir, exclude_generated=False):
                if ref.kind != 'file':
                    raise ContractError('home directory may contain regular files only')
                self.put('home/' + ref.path, safe_read(self.home_dir / ref.path, self.home_dir),
                         executable=ref.executable)
        staged = Path(self.control) / 'credentials' / self.agent.name
        for relative in self.agent.credential_files:
            source = staged / relative
            if not os.path.lexists(source):
                continue
            self.put('home/' + relative, safe_read(source, staged))

    def worker_path(self, relative: str) -> str:
        return str(self.remote / relative)

    def put(self, relative: str, data: bytes, *, executable=False):
        self.rpc('put', path=relative, data=base64.b64encode(data).decode(), executable=executable)

    def read(self, relative: str, *, limit=None) -> bytes:
        data = self.rpc('read', path=relative, limit=limit)
        return base64.b64decode(data, validate=True)

    def files(self, relative: str, *, exclude=False):
        rows = self.rpc('list', path=relative, exclude=exclude)
        return tuple(make(ArtifactRef, **row) for row in rows)

    def verify_seed(self, expected):
        """Compare file identity; provenance describes the recording location."""
        def identities(files):
            return tuple((ref.path, ref.digest, ref.size, ref.executable) for ref in files)

        if identities(self.files("workspace")) != identities(expected):
            raise ContractError("worker seed changed before execution")

    def start_run(self, evidence=None, *, read_only=()):
        """One service supplies mount/tmp/IPC/process lifetime for all native turns."""
        if self.service_active:
            raise ContractError('Run service already started')
        if not Path('/usr/bin/slirp4netns').is_file():
            raise ContractError('Run-isolated internet requires root-owned /usr/bin/slirp4netns; installation is an operational prerequisite')
        evidence = self.run if evidence is None else Path(evidence)
        if self.starts:
            self.bind_units(hashlib.md5(f'{self.run.name}:{self.starts}'.encode(), usedforsecurity=False).hexdigest())
        self.starts += 1
        self.put('resolv.conf', b'nameserver 10.0.2.3\n')
        upstream = evidence / 'artifacts/upstream-resolv.conf'
        # Controller-owned, nonsecret input; slirp drops DAC override capabilities.
        with atomic_output(upstream) as stream:
            stream.write(''.join('nameserver ' + address + '\n' for address in external_nameservers()).encode())
            os.fchmod(stream.fileno(), 0o444)
        self.put('worker_job.py', Path(worker_job.__file__).read_bytes())
        self.put('worker_service.py', Path(worker_service.__file__).read_bytes())
        self.rpc('service_directories')
        request = make(RuntimeRequest, spec=self.spec, name=self.run_unit,
                       workspace=str(self.run / 'workspace'), home=str(self.run / 'home'),
                       argv=(self.spec.python,), log_dir=str(self.run / 'raw'))
        command = self.service_command(request, self.remote, read_only=read_only)
        # Use the same namespace policy with the run supervisor as its entrypoint.
        command[-3:] = ['-c', Path(worker_service.__file__).read_text(), str(self.remote)]
        command.insert(command.index(self.spec.python), '--property=PrivateNetwork=yes')
        checked_command(command, timeout=10)
        self.service_active = True
        state = self.state(self.run_unit)
        pid = state.get('MainPID')
        if not pid or not pid.isdigit() or int(pid) <= 1:
            raise ContractError('Run network namespace has no verified supervisor PID')
        # The host-side network broker cannot connect back to any host address.
        addresses = loads(checked_command(['/usr/sbin/ip', '-j', 'address']), list)
        # Transient properties recognize localhost only as the entire value.
        denied = ['127.0.0.0/8', '::1/128'] + [a['local'] for link in addresses for a in link.get('addr_info', [])]
        source = str(upstream).replace('\\', '\\\\').replace('"', '\\"')
        command = ['/usr/bin/sudo', '-n', '/usr/bin/systemd-run', '--quiet', '--collect', '--unit=' + self.network_unit,
                   '--property=Type=notify', '--property=TimeoutStartSec=30s',
                   '--property=KillMode=control-group', '--property=TimeoutStopSec=3s', '--property=MountFlags=slave',
                   '--property=RuntimeMaxSec=infinity', '--property=IPAddressDeny=' + ' '.join(denied),
                   '--property=BindReadOnlyPaths="' + source + '":/etc/resolv.conf',
                   self.spec.python, '-I', '-c', NETWORK_BROKER,
                   '/usr/bin/slirp4netns', '--configure', '--disable-host-loopback', '--enable-sandbox', '--enable-seccomp', pid, 'tap0']
        checked_command(command, timeout=35)

    def stop_run(self):
        native = self.cleanup(self.run_unit)
        network = self.cleanup(self.network_unit, expected_user='root')
        self.service_active = self.service_active and not (native and network)
        return native and network

    def collect_workspace(self):
        """Replace the controller mirror only after a complete verified transfer."""
        refs = self.files('workspace', exclude=True)
        with tempfile.TemporaryDirectory(prefix='.collect-', dir=self.run) as temp:
            target = Path(temp) / 'workspace'
            self.download_tree(target, refs, relative='workspace')
            if refs != self.files('workspace', exclude=True):
                raise ContractError('workspace changed during collection')
            replace_directory(target, self.run / 'workspace')

    def service_command(self, request: RuntimeRequest, job: Path, *, read_only=()) -> list[str]:
        request = validate(request, RuntimeRequest)
        if request.spec != self.spec or not _UNIT.fullmatch(request.name):
            raise ContractError('invalid runtime binding or unit identity')
        properties = ['Type=exec', 'ExitType=cgroup', 'KillMode=control-group', 'TimeoutStopSec=3s',
                      'SendSIGKILL=yes', 'NoNewPrivileges=yes', 'UMask=0077', 'PrivateTmp=yes',
                      'ProtectHome=yes', 'ProtectSystem=strict', 'ProtectProc=invisible',
                      'PrivateDevices=yes', 'PrivateIPC=yes', 'CapabilityBoundingSet=',
                      'TemporaryFileSystem=/var:ro /run:ro /dev/shm:rw,mode=1777',
                      f'BindPaths={self.remote}', f'ReadWritePaths={self.remote}',
                      f'BindReadOnlyPaths={job / "resolv.conf"}:/etc/resolv.conf',
                      *(f'ReadOnlyPaths={self.remote / path}' for path in read_only),
                      f'User={self.spec.account}', f'Group={self.spec.account}',
                      'RuntimeMaxSec=infinity']
        return ['/usr/bin/sudo', '-n', '/usr/bin/systemd-run', '--quiet', '--collect', '--unit=' + request.name,
                *['--property=' + item for item in properties],
                self.spec.python, '-I', '-c', Path(worker_job.__file__).read_text(), str(job)]

    def state(self, name):
        if not _UNIT.fullmatch(name):
            raise ContractError('invalid unit identity')
        raw = checked_command(['/usr/bin/systemctl', 'show', name + '.service',
                              '--property=LoadState,ActiveState,SubState,User,ControlGroup,Result,MainPID'])
        return dict(line.split('=', 1) for line in raw.decode().splitlines() if '=' in line)

    def cleanup(self, name, *, expected_user=None) -> bool:
        state = self.state(name)
        if state.get('LoadState') == 'not-found':
            return True
        if (state.get('User') or 'root') != (expected_user or self.spec.account):
            raise ContractError('refusing to stop a unit owned by another account')
        try:
            checked_command(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'stop', name + '.service'])
        except RuntimeError:
            if self.state(name).get('LoadState') == 'not-found':
                return True  # PID 1 may collect the stopped unit between show and stop.
            raise
        final = self.state(name)
        return final.get('ActiveState') in {'inactive', 'failed'} and not final.get('ControlGroup')

    def _job(self, request):
        if not request.argv or not Path(request.argv[0]).is_absolute():
            raise ContractError('absolute native binary required')
        if Path(request.home) != self.run / 'home' or Path(request.workspace) != self.run / 'workspace':
            raise ContractError('native request paths belong to another run or role')
        home = self.worker_path('home')
        workspace = self.worker_path('workspace')
        environment = {**control_environment(), **self.agent.home_environment(home),
                       **self.agent.environment, 'TMPDIR': self.worker_path('tmp')}
        if self.spec_path_prefix:
            environment['PATH'] = self.spec_path_prefix + ':' + environment['PATH']
        for name in self.agent.credential_env:
            if name in os.environ:
                environment[name] = os.environ[name]
        job = {'argv': request.argv, 'workspace': workspace, 'environment': environment,
               'host_mount_namespace': os.readlink('/proc/self/ns/mnt'),
               'protected_paths': [str(Path.home()), str(self.control), str(self.run), '/run/user'],
               'stdin': base64.b64encode(request.stdin).decode() if request.stdin is not None else None}
        relative = 'jobs/' + request.name
        if not self.service_active:
            self.put(relative + '/resolv.conf', Path('/etc/resolv.conf').read_bytes())
        self.put(relative + '/request.json', dumps(job))
        self.put(relative + '/ready', b'')
        return relative

    def _observe(self, request, relative, cancel):
        while True:
            if cancel is not None and cancel():
                return {'outcome': 'cancelled', 'exit_code': None, 'error': 'owner cancelled'}
            status = self.rpc('status', path=relative)
            if status['result'] is not None:
                return status['result']
            units = (self.run_unit, self.network_unit) if self.service_active else (request.name,)
            for unit in units:
                state = self.state(unit)
                pid = state.get('MainPID', '')
                if state.get('ActiveState') in {'inactive', 'failed'} or not pid.isdigit() or int(pid) <= 1:
                    final = self.rpc('status', path=relative)
                    if final['result'] is not None:
                        return final['result']
                    return {'outcome': 'harness_error', 'exit_code': None,
                            'error': f'service {unit} exited without a result: {state.get("Result")}; MainPID={pid}'}
            time.sleep(.25)

    def invoke(self, request: RuntimeRequest, cancel=None) -> RuntimeResult:
        request = validate(request, RuntimeRequest)
        if request.spec != self.spec or not _UNIT.fullmatch(request.name):
            raise ContractError("invalid invocation binding")
        if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
            raise ContractError('worker lease does not belong to this run')
        self.rpc('check')
        started = time.monotonic()
        relative = self._job(request)
        record, cleanup, cleanup_error = None, 'pending', None
        try:
            if not self.service_active:
                checked_command(self.service_command(request, self.remote / relative), timeout=10)
            record = self._observe(request, relative, cancel)
        except Halt as exc:
            record = {'outcome': exc.outcome, 'exit_code': None, 'error': str(exc)}
        except Exception as exc:
            record = {'outcome': 'harness_error', 'exit_code': None, 'error': f'{type(exc).__name__}: {exc}'}
        if not self.service_active:
            try:
                cleanup = 'confirmed' if self.cleanup(request.name) else 'failed'
            except Exception as exc:
                cleanup, cleanup_error = 'failed', str(exc)
        folder = Path(request.log_dir)
        paths = {'stdout': None, 'stderr': None}
        try:
            folder.mkdir(parents=True, exist_ok=False)
            for name in paths:
                self.download(relative + '/' + name, folder / name, optional=True,
                              prefix_size=record.get('observation_sizes', {}).get(name))
                paths[name] = str(folder / name)
        except (OSError, RuntimeError, ValueError, MemoryError) as exc:
            record['error'] = '; '.join(value for value in (record['error'], f'log collection: {type(exc).__name__}: {exc}') if value)
            if record['outcome'] == 'completed':
                record['outcome'] = 'harness_error'
        return make(RuntimeResult, outcome=record['outcome'], exit_code=record['exit_code'],
                    wall_sec=time.monotonic() - started, stdout_path=paths['stdout'], stderr_path=paths['stderr'],
                    cleanup=cleanup, cleanup_error=cleanup_error, error=record['error'],
                    interval=make(Interval, kind='native' if self.service_active else 'setup', clock_epoch=record['clock_epoch'],
                                  start=record['started_monotonic'], end=record['finished_monotonic'],
                                  call_id=request.name.removeprefix('bk-')) if 'clock_epoch' in record else None)

    def confirm_stopped(self) -> bool:
        uid = pwd.getpwnam(self.spec.account).pw_uid
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid == uid:
                    return False
            except FileNotFoundError:
                continue
        return True

    def release(self):
        """Only after sealing: discard worker state while retaining private results."""
        with exclusive(self.control / 'owner.lock'):
            if not self.lease.exists():
                return
            if not self.confirm_stopped():
                raise ContractError('worker account is still running; cannot release its files')
            if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
                raise ContractError('worker lease does not belong to this run')
            self.rpc('remove')
            if not self.rpc('empty'):
                raise ContractError('worker state remains; refusing to release the account')
            self.lease.unlink()
