"""Model-free host check for the worker sandbox boundary.

Explicit invocation creates one synthetic execution space. It checks the selected agent
profile's provider endpoints from the worker namespaces.
No models, credentials, automatic recovery, or replacement verification runs are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker_sandbox import contracts as c, hostconfig, profiles
import worker_sandbox.runtime
from worker_sandbox.runtime import NativeRuntime, checked_command
from worker_sandbox.seed import seed_repository


def controller_digest():
    """The sha256 of the runtime.py this doctor imports; recorded, never a gate."""
    return 'sha256:' + hashlib.sha256(Path(worker_sandbox.runtime.__file__).read_bytes()).hexdigest()


def verify(report_path, timeout, profile=None, *, check_binary=False):
    profile = profiles.generic('') if profile is None else profile
    if type(timeout) is not int or timeout <= 0:
        raise ValueError('Host diagnostic timeout must be a positive integer')
    if report_path.exists():
        raise FileExistsError('Choose a new report path; historical evidence is immutable')
    if not Path('/usr/bin/slirp4netns').is_file():
        raise RuntimeError('Missing prerequisite: /usr/bin/slirp4netns')
    root = Path(tempfile.mkdtemp(prefix='worker-sandbox-host-check-'))
    root.chmod(0o700)
    folder = root / 'runs' / uuid.uuid4().hex
    (folder / 'workspace').mkdir(parents=True)
    for name in ('artifacts', 'raw'):
        (folder / name).mkdir()
    seed_repository(folder / 'workspace')
    # The diagnostic executes Python probes only, never a native executable or login.
    spec = hostconfig.read()
    runtime = NativeRuntime(spec, folder, profile, authentication=False)
    report = {'status': 'failed', 'model_calls': 0, 'run': str(folder), 'controller_digest': controller_digest(),
              'profile': profile.name, 'checks': []}
    deadline = time.monotonic() + timeout
    def invoke(label, source):
        name = 'bk-' + uuid.uuid4().hex
        request = c.make(c.RuntimeRequest, spec=runtime.spec, name=name,
            workspace=str(folder / 'workspace'), home=str(folder / 'home'),
            argv=('/usr/bin/python3', '-I', '-c', source), log_dir=str(folder / 'raw' / name))
        result = runtime.invoke(request, cancel=lambda: time.monotonic() >= deadline)
        report['checks'].append({'name': label, 'result': json.loads(c.dumps(result))})
        if result.outcome != 'completed':
            raise RuntimeError(label + ': ' + str(result.error or result.outcome))
        return result
    try:
        runtime.claim()
        invoke('setup-resolver-and-provider-https', """from pathlib import Path
import json,socket,urllib.request,urllib.error
report={'resolver':Path('/etc/resolv.conf').read_text(),'endpoints':[]}
for host in HOSTS:
 addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
 try:
  with urllib.request.urlopen('https://'+host+'/',timeout=10) as response: status=response.status
 except urllib.error.HTTPError as response: status=response.code
 report['endpoints'].append({'host':host,'addresses':addresses,'http_status':status})
print(json.dumps(report))
""".replace('HOSTS', repr(tuple(profile.hosts))))
        runtime.start_run()
        private = folder / 'private-controller-canary'
        private.write_text('private controller fixture')
        paths = [str(private), str(runtime.control), str(Path.home()), '/run/user']
        probe = """import os
from pathlib import Path
paths = PATHS
for path in paths:
 assert not os.access(path, os.R_OK), ('read permission',path)
 assert not os.access(path, os.W_OK), ('write permission',path)
 try:
  fd=os.open(path, os.O_RDONLY|os.O_NONBLOCK)
 except (PermissionError,FileNotFoundError): pass
 else:
  os.close(fd); raise AssertionError(('read succeeded',path))
 target=path if path.endswith('private-controller-canary') else path+'/worker-sandbox-write-probe'
 try:
  fd=os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except (PermissionError,FileNotFoundError,OSError) as exc:
  if exc.errno not in (1,2,13,30): raise
 else:
  os.close(fd); raise AssertionError(('write succeeded',target))
print('actual read and write attempts denied')
""".replace('PATHS', repr(paths))
        invoke('private-controller-files-denial', probe)
        # Publish a same-UID sibling only after this native job passes admission.
        # The canary is readable outside the namespace, so absence is meaningful.
        sibling = runtime.remote.parent / ('probe-' + uuid.uuid4().hex)
        report['cross_run_canary'] = str(sibling)
        source = """import os,time
from pathlib import Path
root=Path(ROOT)
(root/'probe-ready').write_text('ready')
while not (root/'probe-go').exists(): time.sleep(.02)
path=Path(SIBLING)/'session.json'
for flags in (os.O_RDONLY,os.O_WRONLY):
 try: fd=os.open(path,flags)
 except (PermissionError,FileNotFoundError): pass
 else:
  os.close(fd); raise AssertionError('Foreign native session accessible')
print('existing same-UID foreign session read/write denied')
""".replace('ROOT', repr(str(runtime.remote))).replace('SIBLING', repr(str(sibling)))
        failures = []
        def sibling_probe():
            try:
                invoke('cross-run-native-session-read-write-denial', source)
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=sibling_probe)
        thread.start()
        while thread.is_alive() and time.monotonic() < deadline:
            if runtime.rpc('read', path='probe-ready', optional=True, limit=16) is not None:
                break
            time.sleep(.02)
        else:
            raise RuntimeError('Cross-run probe could not establish its ready barrier')
        command = ['/usr/bin/sudo', '-n', '-u', runtime.spec.account, runtime.spec.python, '-I', '-c']
        creator = "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.mkdir(mode=0o700); (p/'session.json').write_text(p.name); assert (p/'session.json').read_text()==p.name"
        checked_command([*command, creator, str(sibling)])
        runtime.put('probe-go', b'go')
        thread.join(max(.01, deadline-time.monotonic()))
        if thread.is_alive() or failures:
            raise RuntimeError('Cross-run access check failed: ' + repr(failures))
        remover = "from pathlib import Path; import sys; p=Path(sys.argv[1]); assert set(x.name for x in p.iterdir())=={'session.json'} and (p/'session.json').read_text()==p.name; (p/'session.json').unlink(); p.rmdir()"
        checked_command([*command, remover, str(sibling)])
        for shared in ('/tmp', '/var/tmp', '/dev/shm'):
            with tempfile.NamedTemporaryFile(dir=shared) as canary:
                os.chmod(canary.name, 0o644)
                invoke('host-canary-' + shared, "from pathlib import Path; assert not Path(" + repr(canary.name) + ").exists()")
        source = "from pathlib import Path; import os; Path('/tmp/same-run').write_text('tmp'); Path('/dev/shm/same-run').write_text('ipc'); Path(os.environ['TMPDIR'],'same-run').write_text('owned')"
        invoke('create-run-temporary-state', source)
        source = "from pathlib import Path; import os; assert Path('/tmp/same-run').read_text()=='tmp'; assert Path('/dev/shm/same-run').read_text()=='ipc'; assert Path(os.environ['TMPDIR'],'same-run').read_text()=='owned'"
        invoke('same-run-tmp-ipc-continuity', source)
        background = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',\"import time; time.sleep(.3); print('delayed',flush=True)\"]); print('parent',flush=True)"
        result = invoke('delayed-background-output', background)
        time.sleep(.6)
        original = Path(result.stdout_path).read_bytes()
        raw = runtime.read('jobs/' + Path(result.stdout_path).parent.name + '/stdout')
        if b'delayed' not in raw or b'delayed' in original:
            raise RuntimeError('Parent observation prefix or delayed raw output is incorrect')
        report['checks'].append({'name': 'immutable-prefix-and-final-output', 'passed': True})
        invoke('internet-dns', "import socket; assert socket.getaddrinfo('api.openai.com',443)")
        addresses = json.loads(checked_command(['/usr/sbin/ip', '-j', 'address']))
        hosts = {'127.0.0.1', '10.0.2.2'} | {row['local'] for link in addresses for row in link.get('addr_info', []) if row['family'] == 'inet'}
        with socket.socket() as listener:
            listener.bind(('0.0.0.0', 0))
            listener.listen()
            port = listener.getsockname()[1]
            source = "import socket\nfor host in " + repr(sorted(hosts)) + ":\n with socket.socket() as s:\n  s.settimeout(.5)\n  assert s.connect_ex((host," + str(port) + ")) != 0, ('host service reachable',host)\n"
            invoke('host-network-services-denied', source)
        invoke('internet-tls-port', "import socket; s=socket.create_connection(('api.openai.com',443),timeout=10); s.close()")
        if check_binary:
            identity = NativeRuntime.verify_model(profile.binary, None)
            report['checks'].append({'name': 'agent-binary-root-owned', 'binary': profile.binary, 'digest': identity, 'passed': True})
        else:
            report['checks'].append({'name': 'agent-binary-root-owned', 'skipped': 'no --binary given', 'passed': True})
        # Informational: managed agent policy on this host is recorded, never required.
        managed = {path: os.path.exists(path) for path in ('/etc/claude-code/managed-settings.json', '/etc/codex')}
        report['checks'].append({'name': 'managed-settings-present', 'present': managed, 'passed': True})
        report['status'] = 'passed'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            stopped = runtime.stop_run() and runtime.confirm_stopped()
            report['checks'].append({'name': 'all-run-processes-stopped', 'passed': stopped})
            if not stopped:
                raise RuntimeError('Stop uncertain; execution space and lease retained')
            if report['status'] == 'passed':
                other = NativeRuntime(runtime.spec, root / 'runs' / uuid.uuid4().hex, profile, authentication=False)
                (other.run / 'workspace').mkdir(parents=True)
                try:
                    other.claim()
                except c.ContractError as exc:
                    if 'leased by another run' not in str(exc):
                        raise
                    report['checks'].append({'name': 'prior-lease-blocks-next-run', 'passed': True})
                else:
                    raise RuntimeError('Another run was admitted before prior removal')
                sibling = runtime.remote.parent / ('probe-' + uuid.uuid4().hex)
                creator = "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.mkdir(mode=0o700); (p/'marker').write_text(p.name)"
                command = ['/usr/bin/sudo', '-n', '-u', runtime.spec.account, runtime.spec.python, '-I', '-c']
                checked_command([*command, creator, str(sibling)])
                try:
                    runtime.claim()
                except RuntimeError as exc:
                    if 'another run or unexpected worker state' not in str(exc):
                        raise
                    report['checks'].append({'name': 'execution-residue-blocks-admission', 'passed': True})
                else:
                    raise RuntimeError('Residue did not block account reuse')
                remover = "from pathlib import Path; import sys; p=Path(sys.argv[1]); assert set(x.name for x in p.iterdir())=={'marker'} and (p/'marker').read_text()==p.name; (p/'marker').unlink(); p.rmdir()"
                checked_command([*command, remover, str(sibling)])
            runtime.collect_workspace()
            if report['status'] == 'passed':
                runtime.release()
                if not runtime.rpc('empty'):
                    raise RuntimeError('Worker residue remains')
                report['checks'].append({'name': 'execution-space-removed', 'passed': True})
        except Exception as exc:
            report['status'] = 'failed'
            report['preservation_error'] = str(exc)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open('x') as stream:
            json.dump(report, stream, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=120, help='Diagnostic observer timeout; not an experiment limit')
    parser.add_argument('--profile', choices=('claude', 'codex', 'generic'), default='generic')
    parser.add_argument('--binary')
    args = parser.parse_args()
    try:
        binary = str(Path(args.binary).absolute()) if args.binary else ''
        profile = {'claude': profiles.claude, 'codex': profiles.codex, 'generic': profiles.generic}[args.profile](binary)
        report = verify(args.report, args.timeout, profile, check_binary=args.binary is not None)
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': report['status'], 'report': str(args.report)}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
