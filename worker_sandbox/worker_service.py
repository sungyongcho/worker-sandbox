"""Run-owned namespace and process lifetime. Contains no planning or model loop."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time


def serve(root: Path):
    children, launched = [], set()
    gateway = subprocess.Popen([sys.executable, '-I', str(root / 'payment_gateway.py'), str(root / 'payment'), '18765'])
    while True:
        if gateway.poll() is not None:
            raise RuntimeError(f'Payment gateway exited: {gateway.returncode}')
        for folder in sorted((root / 'jobs').iterdir()):
            request = folder / 'request.json'
            if folder.name in launched or not (folder / 'ready').is_file():
                continue
            launched.add(folder.name)
            children.append(subprocess.Popen([sys.executable, '-I', str(root / 'worker_job.py'), str(folder)]))
        for child in children[:]:
            if child.poll() is not None:
                children.remove(child)
        time.sleep(.025)


if __name__ == '__main__':
    serve(Path(sys.argv[1]))
