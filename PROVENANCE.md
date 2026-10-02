# Provenance

Source: worker-benchmark-kit at commit 8bb76be (private repository).
Verbatim import commit in this repository: 5ddf473.

| File | Source | Lines | Edits after import |
| --- | --- | --- | --- |
| worker_sandbox/runtime.py | benchkit/runtime.py | 1-661 | none yet |
| worker_sandbox/worker_files.py | benchkit/worker_files.py | 1-612 | none yet |
| worker_sandbox/worker_job.py | benchkit/worker_job.py | 1-60 | none yet |
| worker_sandbox/worker_service.py | benchkit/worker_service.py | 1-29 | none yet |
| worker_sandbox/artifacts.py | benchkit/artifacts.py | 1-187 | none yet |
| worker_sandbox/ownership.py | benchkit/ownership.py | 1-23 | none yet |
| worker_sandbox/seed.py | benchkit/seed.py | 1-26 | none yet |
| worker_sandbox/contracts.py | benchkit/contracts.py | 1-4, 6-11, 13-15, 17-18, 31, 35-48, 60-63, 68-91, 256-266, 505-576, 580-592 | see "contracts.py" below |
| tools/setup_host.py | tools/setup_host.py | 1-59 | none yet |
| tools/doctor.py | tools/verify_host.py | 1-263 | none yet |
| tools/verify_package.py | tools/verify_package.py | 1-136 | none yet |
| tests/test_runtime.py | tests/test_runtime.py | 1-1192 | none yet |
| tests/test_bulk_transfer.py | tests/test_bulk_transfer.py | 1-256 | none yet |
| tests/test_architecture.py | tests/test_architecture.py | 1-68 | none yet |
| tests/fixtures.py | tests/fixtures.py | 1-236 | none yet |
| worker_sandbox/__init__.py | new | | |
| Makefile | Makefile | 1-9 | none |
| requirements-dev.txt | requirements-dev.txt | 1-2 | none |
| .gitignore | .gitignore | 1-13 | added /verification/, /runs/, /sandbox-runs/ |
| pyproject.toml | pyproject.toml | 1-20 | adapted: name, description, script, package; package data dropped |

## contracts.py
- step 3: kept only the section 6.1 subset, copied from the listed source lines in source order
- step 3: dropped the datetime import, FORMAT, Positive and every type and helper not listed in section 6.1
- step 3: Interval.kind Literal['active', 'native', 'jev', 'setup'] -> Literal['native', 'setup']
- step 3: make: deleted the two-line `if issubclass(cls, Document):` block (L577-579 with its body)
- step 3: blank lines between the joined ranges normalized to two
