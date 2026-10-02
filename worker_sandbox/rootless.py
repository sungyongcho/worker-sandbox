"""Rootless execution: user namespaces, nftables, a nested bubblewrap sandbox and slirp4netns instead of root and systemd.

The agent runs as inner uid 1000, which is the controller's first subordinate uid on the host. Only the methods that
touch privilege differ from NativeRuntime; the job protocol, the bridge and every file operation are inherited.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import select
import signal
import stat
import subprocess
import time

from . import rootless_init, worker_files, worker_job, worker_service
from .artifacts import atomic_output, atomic_write, safe_read
from .contracts import ContractError, RuntimeRequest, RuntimeSpec, digest, loads, make, validate
from .runtime import _UNIT, NativeRuntime, checked_command, control_environment, external_nameservers

BINARIES = ('/usr/bin/unshare', '/usr/bin/setpriv', '/usr/bin/newuidmap', '/usr/bin/newgidmap', '/usr/bin/bwrap',
            '/usr/bin/slirp4netns', '/usr/sbin/nft', '/usr/sbin/ip')
APPARMOR = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
BWRAP_PROFILE = Path('/etc/apparmor.d/bwrap-userns-restrict')
DROP = ['--reuid', '1000', '--regid', '1000', '--clear-groups']
SEAL = ['--bounding-set', '-all', '--inh-caps', '-all', '--no-new-privs']

# Starts one setup job in its own session and records its pid, so invoke's launch returns at once like systemd-run.
LAUNCHER = r'''import json, os, subprocess, sys
pidfile, log, argv = sys.argv[1], sys.argv[2], sys.argv[4:]
with open(log, 'xb') as stream:
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
start = open('/proc/%d/stat' % child.pid).read().rsplit(')', 1)[1].split()[19]
fd = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, json.dumps({'pid': child.pid, 'start': start}).encode())
os.close(fd)
'''


def sub_base(path: str, user: str) -> int:
    """The first id of the user's subordinate range; inner 1000 maps to it."""
    for line in Path(path).read_text().splitlines():
        name, _, rest = line.partition(':')
        start, _, count = rest.partition(':')
        if name == user and start.isdigit() and count.isdigit() and int(count) >= 1:
            return int(start)
    raise ContractError(f'{path} has no subordinate range for {user}; add one as root')


def default_control_root() -> Path:
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return state / 'worker-sandbox/controller'


def default_spec() -> RuntimeSpec:
    """Rootless defaults: the control root in the user's state directory, the worker root outside HOME."""
    user = pwd.getpwuid(os.getuid()).pw_name
    return make(RuntimeSpec, account=user, mode='rootless', control_root=str(default_control_root()),
                worker_root=f'/var/tmp/worker-sandbox-{user}/worker',
                subuid_base=sub_base('/etc/subuid', user), subgid_base=sub_base('/etc/subgid', user))


def check_host() -> dict:
    """Host prerequisites of rootless mode that need no provisioned roots."""
    for binary in BINARIES:
        if not os.access(binary, os.X_OK):
            raise ContractError(f'rootless mode requires {binary}')
    for helper in BINARIES[2:4]:
        info = os.stat(helper)
        if info.st_uid != 0 or not info.st_mode & stat.S_ISUID:
            raise ContractError(f'{helper} must be setuid root')
    restricted = APPARMOR.exists() and APPARMOR.read_text().strip() == '1'
    if restricted and not BWRAP_PROFILE.is_file():
        raise ContractError(f'AppArmor restricts user namespaces and {BWRAP_PROFILE} is missing')
    return {'apparmor_restricted': restricted}


def mapping(spec: RuntimeSpec) -> list[str]:
    return [f'--map-users={os.getuid()},0,1', f'--map-users={spec.subuid_base},1000,1',
            f'--map-groups={os.getgid()},0,1', f'--map-groups={spec.subgid_base},1000,1']


def provision(spec: RuntimeSpec) -> dict:
    """Create the private control root and the sub-UID-owned worker root; nothing exists beforehand."""
    control, worker = Path(spec.control_root), Path(spec.worker_root)
    if any(os.path.lexists(path) for path in (control, worker, worker.parent)):
        raise ContractError('rootless roots already exist; inspect them before provisioning')
    control.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    control.mkdir(mode=0o700)
    worker.parent.mkdir(mode=0o711)
    # Inner root creates the worker root and hands it to inner 1000, so its host owner is the sub-UID.
    creator = 'import os,sys; os.mkdir(sys.argv[1], 0o700); os.chown(sys.argv[1], 1000, 1000)'
    checked_command(['/usr/bin/unshare', '--user', *mapping(spec), '--', spec.python, '-I', '-c', creator, str(worker)])
    return {'control_root': str(control), 'worker_root': str(worker), 'worker_owner': worker.lstat().st_uid}


def nft_rules(addresses: list) -> str:
    """The output chain of the outer network namespace, from `ip -j address`."""
    four, six = set(), set()
    for link in addresses:
        for row in link.get('addr_info', []):
            address = ipaddress.ip_address(row['local'])
            (four if address.version == 4 else six).add(str(address))
    lines = ['table inet worker_sandbox {', '  chain out {', '    type filter hook output priority 0; policy accept;',
             '    ip daddr 10.0.2.3 accept', '    ip daddr 10.0.2.2 drop']
    if four:
        lines.append('    ip daddr { ' + ', '.join(sorted(four)) + ' } drop')
    if six:
        lines.append('    ip6 daddr { ' + ', '.join(sorted(six)) + ' } drop')
    lines += ['    ip daddr 127.0.0.0/8 drop', '    ip6 daddr ::1/128 drop', '    ip daddr 10.0.0.0/8 drop',
              '    ip daddr 172.16.0.0/12 drop', '    ip daddr 192.168.0.0/16 drop', '    ip6 daddr fe80::/10 drop',
              '    ip6 daddr fc00::/7 drop', '  }', '}']
    return '\n'.join(lines) + '\n'


def process_start(pid: int) -> str | None:
    """Start time of a live, non-zombie process, or None."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return None
    return None if fields[0] == 'Z' else fields[19]


class RootlessRuntime(NativeRuntime):
    """NativeRuntime with namespaces in place of the worker account, sudo and systemd."""
    def __init__(self, spec, run, agent, **options):
        super().__init__(spec, run, agent, **options)
        if self.spec.mode != 'rootless':
            raise ContractError('RootlessRuntime requires a rootless runtime spec')
        self.outer = None
        self.slirp = None
        self.outer_child = None

    def bridge_command(self):
        script = Path(worker_files.__file__).read_text()
        return ['/usr/bin/unshare', '--user', *mapping(self.spec), '--mount', '--', '/usr/bin/setpriv', *DROP, '--',
                self.spec.python, '-I', '-c', script, str(self.remote)]

    def inspect(self):
        user = pwd.getpwuid(os.getuid()).pw_name
        ranges = {path: sub_base(path, user) for path in ('/etc/subuid', '/etc/subgid')}
        if (ranges['/etc/subuid'], ranges['/etc/subgid']) != (self.spec.subuid_base, self.spec.subgid_base):
            raise ContractError('host.json subordinate bases differ from /etc/subuid and /etc/subgid')
        restricted = check_host()['apparmor_restricted']
        for path, uid, mode in ((self.control, os.getuid(), None), (Path(self.spec.worker_root), self.spec.subuid_base, 0o700),
                                (self.run.parent.parent, os.getuid(), None)):
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
                    or (mode is not None and stat.S_IMODE(info.st_mode) != mode)):
                raise ContractError(f'private owned directory required: {path}')
        return {'mode': 'rootless', 'account': user, 'subuid_base': self.spec.subuid_base,
                'subgid_base': self.spec.subgid_base, 'apparmor_restricted': restricted,
                'runtime_digest': digest(self.spec)}

    @staticmethod
    def verify_model(binary: str, binary_digest: str | None):
        """The sandbox must reach and execute the binary and must not be able to change it."""
        binary = Path(binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ContractError('native binary must be an executable file')
        info = binary.stat()
        if info.st_mode & 0o005 != 0o005 or info.st_mode & 0o002:
            raise ContractError('native binary must be readable and executable, not writable, by other users')
        for path in binary.parents:
            info = path.stat()
            if not info.st_mode & 0o001 or info.st_mode & 0o002 and not info.st_mode & stat.S_ISVTX:
                raise ContractError(f'the sandbox user cannot reach the binary safely through {path}')
        with binary.open('rb') as stream:
            identity = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
        if binary_digest is not None and identity != binary_digest:
            raise ContractError('native executable differs from frozen binary digest')
        return identity

    def units(self) -> Path:
        return self.control / 'units'

    def record_unit(self, name: str, pid: int, **fields):
        self.units().mkdir(mode=0o700, exist_ok=True)
        atomic_write(self.units() / (name + '.json'), json.dumps({'pid': pid, 'start': process_start(pid), **fields}).encode())

    def unit(self, name: str) -> dict | None:
        try:
            return loads(safe_read(self.units() / (name + '.json'), self.units()), dict)
        except FileNotFoundError:
            return None

    def sandbox(self, command: list[str], resolver: Path, *, read_only=()) -> list[str]:
        """The nested bwrap mount sandbox (stage 2 brief Appendix A), ending in the command it runs."""
        target = os.path.realpath('/etc/resolv.conf')
        resolv = ['--tmpfs', '/run', '--ro-bind', str(resolver), target if target.startswith('/run/') else '/etc/resolv.conf',
                  '--remount-ro', '/run']
        binds = ['--bind', str(self.remote), str(self.remote),
                 *(item for path in read_only for item in ('--ro-bind', str(self.remote / path), str(self.remote / path)))]
        binary = Path(self.agent.binary).resolve().parent
        if binary.is_relative_to('/home') or binary.is_relative_to('/tmp') or binary.is_relative_to('/var'):
            binds += ['--ro-bind', str(binary), str(binary)]
        # /var is made read-only after the binds so a worker root under /var/tmp can be mounted into it.
        return ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--die-with-parent',
                '--new-session', '--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/var', '--tmpfs', '/tmp',
                '--tmpfs', '/dev/shm', '--proc', '/proc', '--dev', '/dev', *resolv, *binds, '--remount-ro', '/var',
                '--', *command]

    def service_command(self, request: RuntimeRequest, job: Path, *, read_only=(), network=False) -> list[str]:
        """network=False: a launcher that starts one setup job; network=True: the run service's bwrap argv."""
        request = validate(request, RuntimeRequest)
        if request.spec != self.spec or not _UNIT.fullmatch(request.name):
            raise ContractError('invalid runtime binding or unit identity')
        if network:
            return self.sandbox([self.spec.python, '-I', str(self.remote / 'worker_service.py'), str(self.remote)],
                                self.remote / 'resolv.conf', read_only=read_only)
        chain = ['/usr/bin/unshare', '--user', *mapping(self.spec), '--mount', '--mount-proc', '--pid', '--fork',
                 '--kill-child', '--', '/usr/bin/setpriv', *DROP, *SEAL, '--',
                 *self.sandbox([self.spec.python, '-I', '-c', Path(worker_job.__file__).read_text(), str(job)],
                               job / 'resolv.conf', read_only=read_only)]
        self.units().mkdir(mode=0o700, exist_ok=True)
        log = self.run / 'artifacts' / (request.name + '.log')
        return [self.spec.python, '-I', '-c', LAUNCHER, str(self.units() / (request.name + '.json')), str(log), '--', *chain]

    def start_run(self, evidence=None, *, read_only=()):
        """One outer namespace supplies mount, tmp, IPC, network and process lifetime for all agent turns."""
        if self.service_active:
            raise ContractError('Run service already started')
        evidence = self.run if evidence is None else Path(evidence)
        if self.starts:
            self.bind_units(hashlib.md5(f'{self.run.name}:{self.starts}'.encode(), usedforsecurity=False).hexdigest())
        self.starts += 1
        self.put('resolv.conf', b'nameserver 10.0.2.3\n')
        upstream = evidence / 'artifacts/upstream-resolv.conf'
        # Controller-owned, nonsecret record of the resolvers slirp4netns forwards to.
        with atomic_output(upstream) as stream:
            stream.write(''.join('nameserver ' + address + '\n' for address in external_nameservers()).encode())
            os.fchmod(stream.fileno(), 0o444)
        self.put('worker_job.py', Path(worker_job.__file__).read_bytes())
        self.put('worker_service.py', Path(worker_service.__file__).read_bytes())
        self.put('rootless_init.py', Path(rootless_init.__file__).read_bytes())
        self.put('rules.nft', nft_rules(loads(checked_command(['/usr/sbin/ip', '-j', 'address']), list)).encode())
        self.rpc('service_directories')
        request = make(RuntimeRequest, spec=self.spec, name=self.run_unit,
                       workspace=str(self.run / 'workspace'), home=str(self.run / 'home'),
                       argv=(self.spec.python,), log_dir=str(self.run / 'raw'))
        command = ['/usr/bin/unshare', '--user', *mapping(self.spec), '--net', '--mount', '--mount-proc', '--pid', '--fork',
                   '--kill-child', '--', self.spec.python, '-I', str(self.remote / 'rootless_init.py'),
                   str(self.remote / 'rules.nft'), *self.service_command(request, self.remote, read_only=read_only, network=True)]
        with open(evidence / 'artifacts' / (self.run_unit + '.log'), 'xb') as log:
            self.outer = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                          env=control_environment(), start_new_session=True)
        self.service_active = True
        deadline = time.monotonic() + 10
        while self.outer_child is None and time.monotonic() < deadline and self.outer.poll() is None:
            try:
                children = Path(f'/proc/{self.outer.pid}/task/{self.outer.pid}/children').read_text().split()
            except FileNotFoundError:
                children = []
            self.outer_child = int(children[0]) if children else None
            time.sleep(.02)
        if self.outer_child is None:
            raise ContractError('Run namespace has no verified init process')
        self.record_unit(self.run_unit, self.outer.pid, child=self.outer_child)
        reader, writer = os.pipe()
        try:
            with open(evidence / 'artifacts' / (self.network_unit + '.log'), 'xb') as log:
                self.slirp = subprocess.Popen(['/usr/bin/slirp4netns', '--configure', '--disable-host-loopback',
                                               f'--userns-path=/proc/{self.outer_child}/ns/user', f'--ready-fd={writer}',
                                               str(self.outer_child), 'tap0'],
                                              pass_fds=(writer,), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                              env=control_environment(), start_new_session=True)
            self.record_unit(self.network_unit, self.slirp.pid)
            os.close(writer)
            writer = None
            ready = select.select([reader], [], [], 35)[0]
            if not ready or os.read(reader, 1) != b'1':
                raise ContractError('slirp4netns did not report readiness')
        finally:
            if writer is not None:
                os.close(writer)
            os.close(reader)

    def state(self, name):
        if not _UNIT.fullmatch(name):
            raise ContractError('invalid unit identity')
        for process in (self.outer, self.slirp):
            if process is not None:
                process.poll()  # reap a finished child so it is not seen as alive
        record = self.unit(name)
        if record is None:
            return {'LoadState': 'not-found', 'ActiveState': 'inactive', 'MainPID': '0'}
        main = record.get('child', record['pid'])
        alive = process_start(record['pid']) == record['start'] and process_start(main) is not None
        return {'LoadState': 'loaded', 'ActiveState': 'active' if alive else 'inactive',
                'MainPID': str(main) if alive else '0', 'Result': 'running' if alive else 'exited'}

    def cleanup(self, name, *, expected_user=None) -> bool:
        record = self.unit(name)
        if record is None:
            return True
        pids = [pid for pid in (record['pid'], record.get('child')) if pid is not None]
        if process_start(record['pid']) == record['start']:
            owner = Path(f'/proc/{record["pid"]}/status').read_text().split('Uid:')[1].split()[0]
            if int(owner) != os.getuid():
                raise ContractError('refusing to stop a process owned by another account')
            os.killpg(record['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(self.alive(pid) for pid in pids):
                time.sleep(.05)
            for pid in pids:
                if self.alive(pid):
                    os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(self.alive(pid) for pid in pids):
                time.sleep(.05)
        if any(self.alive(pid) for pid in pids):
            return False
        (self.units() / (name + '.json')).unlink()
        return True

    def alive(self, pid: int) -> bool:
        for process in (self.outer, self.slirp):
            if process is not None and process.pid == pid:
                return process.poll() is None
        return process_start(pid) is not None

    def confirm_stopped(self) -> bool:
        if self.units().is_dir() and any(self.state(path.stem)['ActiveState'] == 'active'
                                         for path in self.units().glob('bk-*.json')):
            return False
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                status = (path / 'status').read_text()
            except (FileNotFoundError, ProcessLookupError):
                continue
            uid = int(status.split('Uid:')[1].split()[0])
            if uid == self.spec.subuid_base and '\nState:\tZ' not in status:
                return False
        return True
