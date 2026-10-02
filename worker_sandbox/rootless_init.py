"""Inner root of the rootless outer namespace: wait for tap0, install the host denial, drop to 1000, exec bwrap.

Shipped into the worker tree and run as `python3 -I rootless_init.py <rules file> <bwrap argv...>`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def main():
    rules, argv = sys.argv[1], sys.argv[2:]
    # slirp4netns creates tap0 in this network namespace from outside; /sys is the host's, so ask ip.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if subprocess.run(['/usr/sbin/ip', '-o', 'link', 'show', 'tap0'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(.1)
    completed = subprocess.run(['/usr/sbin/nft', '-f', rules], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if completed.returncode:
        sys.stderr.write('nft failed: ' + completed.stderr.decode(errors='replace'))
        raise SystemExit(97)
    os.execv('/usr/bin/setpriv', ['/usr/bin/setpriv', '--reuid', '1000', '--regid', '1000', '--clear-groups',
                                  '--bounding-set', '-all', '--inh-caps', '-all', '--no-new-privs', '--', *argv])


if __name__ == '__main__':
    main()
