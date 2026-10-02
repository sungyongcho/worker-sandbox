"""Standard-library stream recorder inside a systemd-owned worker cgroup."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run_job(folder: Path):
    job = json.loads((folder / 'request.json').read_bytes())
    started = time.monotonic()
    outcome, error, code = 'harness_error', None, None
    try:
        data = base64.b64decode(job['stdin'], validate=True) if job['stdin'] is not None else b''
        # A regular stdin file avoids blocking on a provider that never reads.
        (folder / 'stdin').write_bytes(data)
        with (folder / 'stdin').open('rb') as source, (folder / 'stdout').open('wb', buffering=0) as out, (folder / 'stderr').open('wb', buffering=0) as err:
            # Inherited regular file descriptors remain valid after parent exit.
            # Parent completion freezes only an observation prefix, not log lifetime.
            proc = subprocess.Popen(job['argv'], cwd=job['workspace'], env=job['environment'],
                                    stdin=source, stdout=out, stderr=err)
            code = proc.wait()
            outcome = 'completed' if code == 0 else 'provider_error'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    record = {'outcome': outcome, 'exit_code': code, 'error': error, 'wall_sec': time.monotonic() - started,
              'started_monotonic': started, 'finished_monotonic': time.monotonic(),
              'clock_epoch': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'observation_sizes': {name: (folder / name).stat().st_size for name in ('stdout', 'stderr') if (folder / name).exists()}}
    temporary = folder / 'result.tmp'
    temporary.write_text(json.dumps(record, allow_nan=False))
    temporary.rename(folder / 'result.json')
    # PID 1 owns descendant cleanup when the controller stops the service.


def verify_isolation(job):
    """Fail before native execution if the required local isolation is absent."""
    if os.readlink('/proc/self/ns/mnt') == job['host_mount_namespace']:
        raise RuntimeError('worker mount namespace was not isolated')
    if any(not os.statvfs(path).f_flag & os.ST_RDONLY for path in ('/', '/var')):
        raise RuntimeError('worker host filesystem is not read-only')
    if any(os.access(path, os.R_OK) for path in job['protected_paths']):
        raise RuntimeError('worker can access a protected controller path')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    if status['NoNewPrivs'].strip() != '1' or int(status['CapEff'].strip(), 16) != 0:
        raise RuntimeError('worker privileges are not restricted')


def main():
    folder = Path(sys.argv[1])
    verify_isolation(json.loads((folder / 'request.json').read_bytes()))
    run_job(folder)


if __name__ == '__main__':
    main()
