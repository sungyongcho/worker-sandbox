"""Reproduce the measured rootless chain (stage 2 brief, section 3 M8 and Appendix A) once, without root.

Outer `unshare` with a two-entry UID map (controller -> inner 0, sub-UID base -> inner 1000), nftables host
denial installed as inner root, privileges dropped with `setpriv`, a nested `bwrap` mount sandbox, and
`slirp4netns` attached by the controller. A Python probe stands in for the agent and reports what it sees.
Prints one JSON document; exits 0 only when every expected property holds.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time

INSIDE = r'''import json, os, socket, subprocess, sys, time
canary, home, run_dir, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
hosts = json.loads(sys.argv[5])
def status():
    s = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines())
    def ro(path): return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    def hidden(path):
        try: return not os.listdir(path)
        except OSError: return True
    return {'uid': os.getuid(), 'gid': os.getgid(), 'groups': os.getgroups(), 'CapEff': s['CapEff'].strip(),
            'NoNewPrivs': s['NoNewPrivs'].strip(), 'root_ro': ro('/'), 'var_ro': ro('/var'), 'run_ro': ro('/run'),
            'home_hidden': hidden(home), 'tmp_canary_hidden': not os.path.exists(canary),
            'controller_dir_writable': os.access(run_dir, os.W_OK)}
def probe(host, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout): return 'CONNECTED'
    except Exception as exc: return type(exc).__name__ + ':' + str(exc)[:40]
out = {'status': status()}
out['tap0'] = subprocess.run(['ip', '-o', 'link', 'show', 'tap0'], capture_output=True).returncode == 0
try: out['dns'] = sorted({r[4][0] for r in socket.getaddrinfo('api.anthropic.com', 443, type=socket.SOCK_STREAM)})
except Exception as exc: out['dns'] = 'FAILED:' + type(exc).__name__
try:
    with socket.create_connection(('api.anthropic.com', 443), timeout=10): out['tls_internet'] = 'CONNECTED'
except Exception as exc: out['tls_internet'] = type(exc).__name__ + ':' + str(exc)[:40]
out['host_services'] = {host: probe(host) for host in hosts + ['10.0.2.2', '127.0.0.1']}
flush = subprocess.run(['nft', 'flush', 'ruleset'], capture_output=True, text=True)
out['agent_can_flush_nft'] = flush.returncode == 0
print(json.dumps(out), flush=True)
'''


def sub_base(path: str, user: str) -> int:
    for line in Path(path).read_text().splitlines():
        name, start, count = (line.split(':') + ['', '', ''])[:3]
        if name == user and int(count) >= 1001:
            return int(start)
    raise SystemExit(f'{path} has no range of at least 1001 ids for {user}')


def host_addresses() -> tuple[list[str], list[str]]:
    rows = json.loads(subprocess.check_output(['/usr/sbin/ip', '-j', 'address']))
    four, six = [], []
    for link in rows:
        for row in link.get('addr_info', []):
            address = ipaddress.ip_address(row['local'])
            if address.is_loopback:
                continue
            (four if address.version == 4 else six).append(str(address))
    return sorted(set(four)), sorted(set(six))


def rules(four: list[str], six: list[str]) -> str:
    """The stage 2 brief section 5.3 output chain."""
    lines = ['table inet worker_sandbox {', '  chain out {', '    type filter hook output priority 0; policy accept;',
             '    ip daddr 10.0.2.3 accept', '    ip daddr 10.0.2.2 drop']
    if four:
        lines.append('    ip daddr { ' + ', '.join(four) + ' } drop')
    if six:
        lines.append('    ip6 daddr { ' + ', '.join(six) + ' } drop')
    lines += ['    ip daddr 127.0.0.0/8 drop', '    ip6 daddr ::1/128 drop', '    ip daddr 10.0.0.0/8 drop',
              '    ip daddr 172.16.0.0/12 drop', '    ip daddr 192.168.0.0/16 drop', '    ip6 daddr fe80::/10 drop',
              '    ip6 daddr fc00::/7 drop', '  }', '}']
    return '\n'.join(lines) + '\n'


def children(pid: int) -> list[int]:
    try:
        return [int(x) for x in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
    except FileNotFoundError:
        return []


def run(timeout: float, variant: str) -> dict:
    user = pwd.getpwuid(os.getuid()).pw_name
    uid_base, gid_base = sub_base('/etc/subuid', user), sub_base('/etc/subgid', user)
    four, six = host_addresses()
    work = Path(tempfile.mkdtemp(prefix='worker-sandbox-probe-'))
    work.chmod(0o755)  # inner 1000 is host uid_base and must read the probe files
    canary = Path(tempfile.mkstemp(prefix='worker-sandbox-probe-canary-', dir='/tmp')[1])
    canary.chmod(0o644)
    (work / 'inside.py').write_text(INSIDE)
    (work / 'rules.nft').write_text(rules(four, six))
    (work / 'resolv.conf').write_text('nameserver 10.0.2.3\n')
    listeners = []
    for family, host in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
        listener = socket.socket(family)
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, listeners[0].getsockname()[1] if listeners else 0))
        listener.listen()
        listeners.append(listener)
    port = listeners[0].getsockname()[1]
    resolver = str(work / 'resolv.conf')
    if variant == 'm8':
        # Appendix A exactly: /run stays the host's.
        mounts = ['--ro-bind', resolver, '/etc/resolv.conf']
    else:
        # Runtime variant: /run is a read-only tmpfs; when /etc/resolv.conf links into /run, the resolver is bound
        # at the link target inside the new tmpfs before it is made read-only.
        target = os.path.realpath('/etc/resolv.conf')
        mounts = ['--tmpfs', '/run', '--ro-bind', resolver, target if target.startswith('/run/') else '/etc/resolv.conf',
                  '--remount-ro', '/run']
    bwrap = ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--die-with-parent',
             '--new-session', '--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/var', '--remount-ro', '/var',
             '--tmpfs', '/tmp', '--tmpfs', '/dev/shm', '--proc', '/proc', '--dev', '/dev', *mounts,
             '--bind', str(work), str(work), '--', '/usr/bin/python3', '-I', str(work / 'inside.py'), str(canary),
             str(Path.home()), str(work), str(port), json.dumps(four + six)]
    outer = ('for i in $(seq 1 100); do ip -o link show tap0 >/dev/null 2>&1 && break; sleep 0.1; done\n'
             f'nft -f {work}/rules.nft || {{ echo "nft failed" >&2; exit 97; }}\n'
             'exec /usr/bin/setpriv --reuid 1000 --regid 1000 --clear-groups --bounding-set -all --inh-caps -all'
             ' --no-new-privs -- ' + ' '.join("'" + arg.replace("'", "'\\''") + "'" for arg in bwrap) + '\n')
    (work / 'outer.sh').write_text(outer)
    mapping = [f'--map-users={os.getuid()},0,1', f'--map-users={uid_base},1000,1',
               f'--map-groups={os.getgid()},0,1', f'--map-groups={gid_base},1000,1']
    report = {'variant': variant, 'subuid_base': uid_base, 'subgid_base': gid_base, 'host_ipv4': four, 'host_ipv6': six,
              'rules': rules(four, six)}
    process = subprocess.Popen(['/usr/bin/unshare', '--user', *mapping, '--net', '--mount', '--mount-proc', '--pid',
                                '--fork', '--kill-child', '--', '/bin/sh', str(work / 'outer.sh')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    slirp = None
    try:
        deadline = time.monotonic() + 5
        while not children(process.pid) and time.monotonic() < deadline:
            time.sleep(.02)
        child, = children(process.pid)
        reader, writer = os.pipe()
        slirp = subprocess.Popen(['/usr/bin/slirp4netns', '--configure', '--disable-host-loopback',
                                  f'--userns-path=/proc/{child}/ns/user', f'--ready-fd={writer}', str(child), 'tap0'],
                                 pass_fds=(writer,), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.close(writer)
        report['slirp_ready'] = os.read(reader, 1).decode()
        os.close(reader)
        stdout, stderr = process.communicate(timeout=timeout)
        report['exit'] = process.returncode
        report['stderr'] = stderr.decode(errors='replace')[-2000:]
        report['inside'] = json.loads(stdout) if stdout.strip() else None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if slirp is not None:
            slirp.terminate()
            report['slirp_stderr'] = slirp.communicate(timeout=5)[1].decode(errors='replace')[-500:]
        for listener in listeners:
            listener.close()
        canary.unlink()
        shutil.rmtree(work)
    report['passed'] = passed(report)
    return report


def passed(report: dict) -> bool:
    inside = report.get('inside') or {}
    status = inside.get('status', {})
    services = inside.get('host_services', {})
    return (report.get('exit') == 0 and report.get('slirp_ready') == '1' and status.get('uid') == 1000
            and status.get('CapEff') == '0000000000000000' and status.get('NoNewPrivs') == '1'
            and all(status.get(key) for key in ('root_ro', 'var_ro', 'home_hidden', 'tmp_canary_hidden'))
            # The probe directory is controller-owned; the sub-UID agent must not be able to write it.
            and status.get('controller_dir_writable') is False
            and (report['variant'] == 'm8' or status.get('run_ro') is True)
            and inside.get('tap0') is True and isinstance(inside.get('dns'), list)
            and inside.get('tls_internet') == 'CONNECTED' and bool(services)
            and all(result != 'CONNECTED' for result in services.values())
            and inside.get('agent_can_flush_nft') is False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=60, help='Probe timeout; not an agent limit')
    parser.add_argument('--variant', choices=('m8', 'runtime'), default='m8',
                        help='m8: Appendix A exactly; runtime: adds the read-only /run tmpfs of section 4.1')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = run(args.timeout, args.variant)
    text = json.dumps(report, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x') as stream:
            stream.write(text)
    print(text)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
