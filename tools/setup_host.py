"""Create the dedicated worker account and private controller control directory.

Run once as root with --controller USER. No sudoers, credentials, provider
installations, existing accounts, or personal configuration are modified.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import subprocess


ACCOUNT = 'worker-sandbox'
WORKER = Path('/var/lib/worker-sandbox-worker')
CONTROL = Path('/var/lib/worker-sandbox-controller')
PYTHON = '/usr/bin/python3'


def prepare(controller: str, account: str = ACCOUNT, worker: Path = WORKER, control: Path = CONTROL):
    if os.geteuid() != 0:
        raise SystemExit('Run this explicit provisioning command through sudo.')
    owner = pwd.getpwnam(controller)
    if owner.pw_uid == 0 or controller == account:
        raise SystemExit('The controller must be an existing, separate non-root user.')
    try:
        pwd.getpwnam(account)
    except KeyError:
        pass
    else:
        raise SystemExit('Account already exists; inspect it rather than overwriting it.')
    if any(path.exists() or path.is_symlink() for path in (worker, control)):
        raise SystemExit('Managed paths already exist; inspect them before provisioning.')
    try:
        grp.getgrnam(account)
    except KeyError:
        pass
    else:
        raise SystemExit('Worker group already exists; inspect it before provisioning.')
    subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                    '--home-dir', str(worker), '--shell', '/usr/sbin/nologin', account], check=True)
    worker_account = pwd.getpwnam(account)
    for path, uid, gid in ((worker, worker_account.pw_uid, worker_account.pw_gid), (control, owner.pw_uid, owner.pw_gid)):
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
    print(f'Created {account} (uid={worker_account.pw_uid}), {worker}, {control}.')
    print('No provider CLI or authentication has been installed. No sudoers rule was added.')


def host_document(account: str, worker: Path, control: Path, python: str = PYTHON) -> bytes:
    """The RuntimeSpec bytes contracts.dumps writes, produced without the package's msgspec dependency."""
    return json.dumps({'account': account, 'control_root': str(control), 'python': python, 'worker_root': str(worker)},
                      ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


def write_host(controller: str, account: str = ACCOUNT, worker: Path = WORKER, control: Path = CONTROL,
               python: str = PYTHON) -> Path:
    """Record the provisioned names in <control>/host.json once, owned by the controller."""
    owner = pwd.getpwnam(controller)
    target = control / 'host.json'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(host_document(account, worker, control, python))
        os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
    print(f'Recorded {target}.')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', required=True)
    parser.add_argument('--account', default=ACCOUNT)
    parser.add_argument('--worker-root', type=Path, default=WORKER)
    parser.add_argument('--control-root', type=Path, default=CONTROL)
    parser.add_argument('--python', default=PYTHON)
    args = parser.parse_args()
    prepare(args.controller, args.account, args.worker_root, args.control_root)
    write_host(args.controller, args.account, args.worker_root, args.control_root, args.python)


if __name__ == '__main__':
    main()
