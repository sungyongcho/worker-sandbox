"""Create the dedicated worker account and private controller control directory.

Run once as root with --controller USER. No sudoers, credentials, provider
installations, existing accounts, or personal configuration are modified.
"""
from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import pwd
import subprocess


ACCOUNT = 'food-delivery'
WORKER = Path('/var/lib/benchkit-worker')
CONTROL = Path('/var/lib/benchkit-controller')


def prepare(controller: str):
    if os.geteuid() != 0:
        raise SystemExit('Run this explicit provisioning command through sudo.')
    owner = pwd.getpwnam(controller)
    if owner.pw_uid == 0 or controller == ACCOUNT:
        raise SystemExit('The controller must be an existing, separate non-root user.')
    try:
        pwd.getpwnam(ACCOUNT)
    except KeyError:
        pass
    else:
        raise SystemExit('Account already exists; inspect it rather than overwriting it.')
    if any(path.exists() or path.is_symlink() for path in (WORKER, CONTROL)):
        raise SystemExit('Managed paths already exist; inspect them before provisioning.')
    try:
        grp.getgrnam(ACCOUNT)
    except KeyError:
        pass
    else:
        raise SystemExit('Worker group already exists; inspect it before provisioning.')
    subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                    '--home-dir', str(WORKER), '--shell', '/usr/sbin/nologin', ACCOUNT], check=True)
    worker = pwd.getpwnam(ACCOUNT)
    for path, uid, gid in ((WORKER, worker.pw_uid, worker.pw_gid), (CONTROL, owner.pw_uid, owner.pw_gid)):
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
    print(f'Created {ACCOUNT} (uid={worker.pw_uid}), {WORKER}, {CONTROL}.')
    print('No provider CLI or authentication has been installed. No sudoers rule was added.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', required=True)
    args = parser.parse_args()
    prepare(args.controller)


if __name__ == '__main__':
    main()
