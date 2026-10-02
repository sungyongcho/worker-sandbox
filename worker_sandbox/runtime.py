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
import threading
import time
import uuid

from . import worker_files, worker_job, worker_service, seed, payment_gateway
from .adapters import EVIDENCE_DIRECTORIES, auth_command
from .artifacts import atomic_output, atomic_write, reference_file, replace_directory, safe_open, safe_read, tree_manifest
from .contracts import ArtifactRef, ContractError, Halt, Interval, NativeSpec, RuntimeRequest, RuntimeResult, RuntimeSpec, digest, dumps, loads, make, validate
from .credentials import AUTH_FILE, AccountMismatch, CredentialVault, MalformedCredentials, identity, LIMIT, normalize
from .ownership import exclusive
from .payment_fixture import PaymentFixture
from .secrets import credential_secrets, unparsed_secrets, verify_no_known_secret

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


def native_home(home: str) -> dict[str, str]:
    """Point every native state location into one private HOME."""
    return {'HOME': home, 'CODEX_HOME': home + '/.codex',
            'XDG_CONFIG_HOME': home + '/.config', 'XDG_DATA_HOME': home + '/.local/share',
            'XDG_CACHE_HOME': home + '/.cache', 'XDG_STATE_HOME': home + '/.local/state'}


def checked_command(argv: list[str], *, data=None, timeout=30) -> bytes:
    completed = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=control_environment(), timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f'{Path(argv[0]).name} failed ({completed.returncode}): '
                           + completed.stderr.decode(errors='replace')[:1000])
    return completed.stdout


def native_login(spec: RuntimeSpec, model: NativeSpec) -> dict:
    """One-time device login outside any run into a private staging CODEX_HOME, imported into the vault."""
    control = Path(spec.control_root)
    with exclusive(control / 'owner.lock'):
        if (control / 'lease.json').exists():
            raise ContractError('login runs outside any run; finish or recover the leased run first')
        NativeRuntime.verify_model(model)
        vault = CredentialVault(control)
        previous = vault.load()
        with tempfile.TemporaryDirectory(prefix='.login-', dir=control) as staging:
            (Path(staging) / '.codex').mkdir(mode=0o700)
            # The terminal is inherited; the device code and credentials never reach kit files.
            completed = subprocess.run(auth_command(model, login=True), cwd=staging, check=False,
                                       env={**control_environment(), **native_home(staging)})
            if completed.returncode:
                raise ContractError(f'native login failed ({completed.returncode}); vault unchanged')
            try:
                raw = safe_read(Path(staging) / AUTH_FILE, Path(staging), LIMIT)
            except FileNotFoundError:
                raise ContractError('native login left no credential file; vault unchanged') from None
            account = vault.replace(raw)
    return {'account_id': account,
            'previous_account_id': identity(previous) if previous is not None else None}


class NativeRuntime:
    """Owns the worker filesystem and systemd units; never reads or writes run DBs."""
    def __init__(self, spec: RuntimeSpec, run: Path, *, authentication=True, profile=()):
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
        self.payment_url = None
        self.payment = None
        self.handled_payments = set()
        self.authentication = authentication
        self.profile = tuple(validate(ref, ArtifactRef) for ref in profile)
        self.payment_lock = threading.Lock()

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

    def scan_tree(self, root: Path, refs, secrets):
        """Check all staged originals before publishing any transferred evidence."""
        for ref in refs:
            if any(value in ref.path.encode() for value in secrets):
                raise ContractError('Known authentication material detected in file path')
            if ref.kind == 'symlink':
                if any(value in ref.target.encode() for value in secrets):
                    raise ContractError('Known authentication material detected in symlink target')
            else:
                with safe_open(root / ref.path, root) as stream:
                    verify_no_known_secret(stream, secrets)

    def inspect(self):
        try:
            account = pwd.getpwnam(self.spec.account)
        except KeyError as exc:
            raise ContractError("food-delivery account is not provisioned") from exc
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
                'runtime_digest': digest(self.spec), 'disk_hard_quota': False}

    @staticmethod
    def verify_model(model):
        binary = Path(model.binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ContractError('native binary must be an executable file')
        for path in (binary, *binary.parents):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ContractError('native installation must be root-owned and not group/world writable')
        with binary.open('rb') as stream:
            identity = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
        if identity != model.binary_digest:
            raise ContractError('native executable differs from frozen binary digest')
        return identity

    def claim(self):
        self.inspect()
        with exclusive(self.control / 'owner.lock'):
            if self.lease.exists():
                if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
                    raise ContractError('food-delivery is leased by another run; finish or recover it first')
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

    def reset(self, *, keep=None):
        """Recreate this run's whole worker root from the pristine workspace, optionally keeping one top-level entry."""
        if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
            raise ContractError('worker lease does not belong to this run')
        if self.service_active or not self.confirm_stopped():
            raise ContractError('worker reset requires a stopped run service and no worker processes')
        self.rpc('reset', keep=keep)
        self.populate()

    def seed_native_state(self):
        """Inject only the selected credentials, and a CUSTOM cell's frozen profile, into the implementer HOME."""
        home = 'homes/swe/.codex/'
        for ref in self.profile:
            data = safe_read(self.run / 'profile' / ref.path, self.run / 'profile')
            if (digest(data), len(data)) != (ref.digest, ref.size):
                raise ContractError('run implementer profile differs from its frozen identity')
            self.put(home + ref.path, data, executable=ref.executable)
        if self.profile:
            seeded = self.rpc('file_refs', paths=[home + ref.path for ref in self.profile])
            if [(row['digest'], row['size'], row['executable']) for row in seeded] != [
                    (ref.digest, ref.size, ref.executable) for ref in self.profile]:
                raise ContractError('seeded implementer profile differs from its frozen identity')
        raw = CredentialVault(self.control).load()
        if raw is not None:
            self.remember(raw)
            self.put('homes/swe/' + AUTH_FILE, raw)

    def remember(self, raw):
        """Keep every injected or read-back credential value in this run's private scan set."""
        CredentialVault(self.control).remember(self.run.name, credential_secrets(raw))

    def credential_bytes(self):
        raw = self.rpc('read', path='homes/swe/' + AUTH_FILE, limit=LIMIT, optional=True)
        return base64.b64decode(raw, validate=True) if raw is not None else None

    def read_credentials(self):
        raw = self.credential_bytes()
        return normalize(raw) if raw is not None else None

    def sync_credentials(self):
        """After each native call, carry a same-account refresh back to the vault; newest wins."""
        if loads(safe_read(self.lease, self.control), dict) != {'run': str(self.run)}:
            raise ContractError('worker lease does not belong to this run')
        raw = self.read_credentials()
        if raw is None:
            return 'missing'
        self.remember(raw)
        return 'refreshed' if CredentialVault(self.control).refresh(raw) else 'unchanged'

    def authentication_secrets(self):
        """Scan every injected and read-back credential, plus the vault value, before publishing evidence.

        A HOME credential that no longer parses is still scanned for, so it never blocks collection."""
        values = set(CredentialVault(self.control).seen(self.run.name))
        for raw in (CredentialVault(self.control).load(), self.credential_bytes()):
            if raw is not None:
                try:
                    values.update(credential_secrets(raw))
                except MalformedCredentials:
                    values.update(unparsed_secrets(raw))
        return tuple(values)

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
        """One service supplies mount/tmp/IPC/process lifetime for all native turns.

        A restart after a confirmed stop gets new unit names, and its payment ledger and
        resolver go under the given local evidence folder, so no earlier start's files collide.
        """
        if self.service_active:
            raise ContractError('Run service already started')
        if not Path('/usr/bin/slirp4netns').is_file():
            raise ContractError('Run-isolated internet requires root-owned /usr/bin/slirp4netns; installation is an operational prerequisite')
        evidence = self.run if evidence is None else Path(evidence)
        if self.starts:
            self.bind_units(hashlib.md5(f'{self.run.name}:{self.starts}'.encode(), usedforsecurity=False).hexdigest())
        self.starts += 1
        self.payment_url = 'http://127.0.0.1:18765'
        self.payment = PaymentFixture(evidence / 'artifacts/payment-ledger.sqlite3', self.run.name)
        self.handled_payments = set()
        self.put('resolv.conf', b'nameserver 10.0.2.3\n')
        upstream = evidence / 'artifacts/upstream-resolv.conf'
        # Controller-owned, nonsecret input; slirp drops DAC override capabilities.
        with atomic_output(upstream) as stream:
            stream.write(''.join('nameserver ' + address + '\n' for address in external_nameservers()).encode())
            os.fchmod(stream.fileno(), 0o444)
        self.put('worker_job.py', Path(worker_job.__file__).read_bytes())
        self.put('worker_service.py', Path(worker_service.__file__).read_bytes())
        self.put('payment_gateway.py', Path(payment_gateway.__file__).read_bytes())
        self.rpc('service_directories')
        request = make(RuntimeRequest, spec=self.spec, name=self.run_unit,
                       workspace=str(self.run / 'workspace'), home=str(self.run / 'homes/swe'),
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

    def handle_payments(self):
        with self.payment_lock:
            self._handle_payments()

    def _handle_payments(self):
        if self.payment is None:
            return
        for identity in self.rpc('payment_requests'):
            if identity in self.handled_payments:
                continue
            request = loads(self.read('payment/' + identity + '/request.json'), dict)
            if set(request) != {'path', 'body'} or request['path'] not in {'/charge', '/refund'}:
                raise ContractError('Invalid payment gateway operation')
            status, body = self.payment.request(request['path'], request['body'])
            self.put('payment/' + identity + '/response.json', dumps({'status': status, 'body': body}))
            self.handled_payments.add(identity)


    def stop_run(self):
        native = self.cleanup(self.run_unit)
        network = self.cleanup(self.network_unit, expected_user='root')
        self.service_active = self.service_active and not (native and network)
        return native and network

    def collect_native_evidence(self, archive=None):
        """Archive complete stopped native files, excluding authentication explicitly."""
        if not self.confirm_stopped():
            raise ContractError('Native evidence collection requires stopped processes')
        references = []
        secrets = self.authentication_secrets() if self.authentication else ()
        directories = ['jobs', 'workspace/.git', *('homes/swe/' + p for p in EVIDENCE_DIRECTORIES)]
        paths = self.rpc('native_evidence', directories=directories)
        refs = tuple(make(ArtifactRef, **row) for row in self.rpc('file_refs', paths=paths))
        if any(ref.kind != 'file' for ref in refs):
            raise ContractError('Native evidence must contain regular files')
        archive = self.run / 'archive' if archive is None else Path(archive)
        with tempfile.TemporaryDirectory(prefix='.archive-', dir=self.run) as temp:
            staging = Path(temp) / 'tree'
            self.download_tree(staging, refs)
            self.scan_tree(staging, refs, secrets)
            # Validate every pre-existing original before publishing any new entry.
            for ref in refs:
                target = archive / ref.path
                if os.path.lexists(target):
                    old = reference_file(target, archive)
                    if (old.digest, old.size, old.executable) != (ref.digest, ref.size, ref.executable):
                        raise ContractError('Archived native evidence differs from stopped source')
            for ref in refs:
                target = archive / ref.path
                if not os.path.lexists(target):
                    os.close(worker_files.directory_fd(target.parent, create=True))
                    with safe_open(staging / ref.path, staging) as source, atomic_output(target) as output:
                        for chunk in worker_files.file_chunks(source.fileno()):
                            output.write(chunk)
                        os.fchmod(output.fileno(), 0o700 if ref.executable else 0o600)
                references.append(reference_file(target, self.run))
        return tuple(references)

    def model_evidence(self, session_id, destination):
        relative = self.rpc('session_evidence', session_id=session_id)
        if relative is None:
            return None
        return self.download(relative, destination)

    def execution_request(self, unit_name):
        return self.read('jobs/' + unit_name + '/request.json')

    def collect_workspace(self):
        """Replace the controller mirror only after a complete verified transfer."""
        refs = self.files('workspace', exclude=True)
        with tempfile.TemporaryDirectory(prefix='.collect-', dir=self.run) as temp:
            target = Path(temp) / 'workspace'
            self.download_tree(target, refs, relative='workspace')
            self.scan_tree(target, refs, self.authentication_secrets() if self.authentication else ())
            if refs != self.files('workspace', exclude=True):
                raise ContractError('workspace changed during collection')
            replace_directory(target, self.run / 'workspace')

    def observe_workspace(self):
        return self.rpc('observe_workspace')

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
        if Path(request.home) != self.run / 'homes/swe' or Path(request.workspace) != self.run / 'workspace':
            raise ContractError('native request paths belong to another run or role')
        home = self.worker_path('homes/swe')
        workspace = self.worker_path('workspace')
        environment = {**control_environment(), 'PATH': '/opt/benchkit-python/bin:/usr/local/bin:/usr/bin:/bin',
                       **native_home(home), 'TMPDIR': self.worker_path('tmp')}
        if self.service_active:
            environment['PAYMENT_PROVIDER_URL'] = self.payment_url
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
            if self.service_active:
                self.handle_payments()
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
        if self.authentication and self.lease.exists() and not self.rpc('empty'):
            self._release_credentials()
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
            if self.authentication:
                CredentialVault(self.control).forget(self.run.name)
            self.lease.unlink()

    def _release_credentials(self):
        """Final read-back; a missing, malformed or other-account file is recorded, never a reason to retain the lease."""
        if not self.confirm_stopped():
            raise ContractError('stop worker processes before releasing credentials')
        try:
            detail = {'missing': 'implementer credential file was missing at release; vault unchanged'}.get(
                self.sync_credentials())
        except AccountMismatch as exc:
            detail = str(exc)
        except MalformedCredentials:
            name = CredentialVault(self.control).keep_malformed(self.credential_bytes())
            detail = f'implementer credential file was malformed at release; private copy preserved as {name}; vault unchanged'
        if detail is not None:
            atomic_write(self.run / 'artifacts' / f'credential-release-{uuid.uuid4().hex}.json',
                         dumps({'run_id': self.run.name, 'detail': detail}))
