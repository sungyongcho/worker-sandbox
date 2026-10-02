"""Build the wheel, install it into an empty environment, import it, and run the offline suite once.

Run with a development Python that has `build` installed. No privileged host operation,
native CLI or model call is made; the suite runs with network and live native execution blocked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

IMPORT_CHECK = r'''
from importlib import import_module, metadata
import json
from pathlib import Path
import sys
import worker_sandbox

source = Path(sys.argv[1])
package = Path(worker_sandbox.__file__).resolve().parent
assert package.is_relative_to(Path(sys.prefix).resolve()), "source checkout shadowed the installed package"
modules = sorted(path.stem for path in (source / "worker_sandbox").glob("*.py"))
assert sorted(path.stem for path in package.glob("*.py")) == modules
for name in modules:
    module = import_module("worker_sandbox" if name == "__init__" else "worker_sandbox." + name)
    assert Path(module.__file__).read_bytes() == (source / "worker_sandbox" / (name + ".py")).read_bytes(), name
print(json.dumps({"version": metadata.version("worker-sandbox"), "modules": len(modules),
                  "requires": metadata.requires("worker-sandbox")}))
'''

OFFLINE_TESTS = r'''
import json
from pathlib import Path
import sys
import unittest
import worker_sandbox

test_root, temporary = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
assert Path(worker_sandbox.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())

def offline_guard(event, arguments):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("network access is forbidden in offline package tests")
    if event == "subprocess.Popen":
        executable = Path(arguments[0]).absolute()
        if executable.name in {"sudo", "systemctl", "systemd-run", "codex", "claude", "unshare", "bwrap", "slirp4netns", "nft", "setpriv", "newuidmap"} and not executable.is_relative_to(temporary):
            raise RuntimeError("live privileged/provider executable is forbidden in offline package tests")

sys.addaudithook(offline_guard)
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(test_root)))
print(json.dumps({"tests_run": result.testsRun, "failures": len(result.failures),
                  "errors": len(result.errors), "skipped": len(result.skipped)}))
raise SystemExit(0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 1)
'''


def verify(report_path: Path) -> dict:
    report_path = report_path.absolute()
    if report_path.exists():
        raise FileExistsError('Choose a new report path; existing verification evidence is preserved')
    facts, commands = {}, []
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV", "PIP_TARGET",
                                  "PIP_PREFIX", "PIP_USER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
                       PIP_NO_INPUT="1", SOURCE_DATE_EPOCH="946684800")
    with tempfile.TemporaryDirectory(prefix="worker-sandbox-verification-") as name:
        temporary = Path(name)
        empty, fresh, scratch = temporary / "empty", temporary / "environment", temporary / "tmp"
        empty.mkdir()
        scratch.mkdir()
        environment["TMPDIR"] = str(scratch)
        def run(label: str, arguments: list[str]) -> str:
            print(label, flush=True)
            commands.append(" ".join(arguments).replace(str(temporary), "<temporary-directory>"))
            completed = subprocess.run(arguments, cwd=empty, env=environment, text=True, capture_output=True)
            if completed.returncode:
                raise RuntimeError(f"{label} failed ({completed.returncode})\n{completed.stdout}\n{completed.stderr}")
            return completed.stdout
        try:
            run("Build the wheel in an isolated build environment",
                [sys.executable, "-m", "build", "--wheel", "--outdir", str(temporary / "wheels"), str(ROOT)])
            wheel, = (temporary / "wheels").glob("*.whl")
            run("Create an empty virtual environment", [sys.executable, "-m", "venv", str(fresh)])
            python = str(fresh / "bin/python")
            requirements = [line for line in (ROOT / "requirements-dev.txt").read_text().split() if line.startswith("jsonschema==")]
            run("Install the wheel and the test dependency",
                [python, "-I", "-m", "pip", "install", str(wheel), *requirements])
            check = temporary / "import_check.py"
            check.write_text(IMPORT_CHECK, encoding="utf-8")
            facts["installed"] = json.loads(run("Import every installed module and compare its bytes",
                                                [python, "-I", str(check), str(ROOT)]))
            tests = temporary / "offline_tests.py"
            tests.write_text(OFFLINE_TESTS, encoding="utf-8")
            output = run("Run the offline suite against the installed package",
                         [python, "-I", str(tests), str(ROOT / "tests"), str(temporary)])
            facts["tests"] = json.loads(output.strip().splitlines()[-1])
            facts["wheel"] = wheel.name
            facts["status"] = "passed"
        except Exception as exc:
            facts["status"] = "failed"
            facts["error"] = str(exc).replace(str(temporary), "<temporary-directory>")
            raise
        finally:
            facts["verified_utc"] = datetime.now(timezone.utc).isoformat()
            facts["python"] = sys.version.split()[0]
            facts["commands"] = commands
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(facts, stream, indent=2)
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        facts = verify(arguments.report)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": facts["status"], "tests": facts["tests"], "report": str(arguments.report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
