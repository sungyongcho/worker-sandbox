# Provenance

Source: worker-benchmark-kit at commit 8bb76be (private repository).
Verbatim import commit in this repository: 5ddf473.

| File | Source | Lines | Edits after import |
| --- | --- | --- | --- |
| worker_sandbox/runtime.py | benchkit/runtime.py | 1-661 | see "runtime.py" below |
| worker_sandbox/worker_files.py | benchkit/worker_files.py | 1-612 | see "worker_files.py" below |
| worker_sandbox/worker_job.py | benchkit/worker_job.py | 1-60 | none yet |
| worker_sandbox/worker_service.py | benchkit/worker_service.py | 1-29 | see "worker_service.py" below |
| worker_sandbox/artifacts.py | benchkit/artifacts.py | 1-8, 10-125, 153-162, 172-180 | see "artifacts.py" below |
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

## runtime.py
- step 4: L20 import: dropped payment_gateway
- step 4: deleted L26 `from .payment_fixture import PaymentFixture`
- step 4: __init__: deleted L123-125 (payment_url, payment, handled_payments) and L128 (payment_lock)
- step 4: start_run docstring: kept the first sentence on L358 and closed it there; deleted L359-362 (blank line, the payment ledger sentence, closing quotes)
- step 4: start_run: deleted L371-373 (payment_url, PaymentFixture, handled_payments) and L382 (payment_gateway.py upload)
- step 4: deleted handle_payments and _handle_payments (L411-426) with the two blank lines after them (L427-428)
- step 4: _job: deleted L541-542 (PAYMENT_PROVIDER_URL)
- step 4: _observe: deleted L558-559 (handle_payments call)
- step 5: deleted L21 `from .adapters import EVIDENCE_DIRECTORIES, auth_command` (last users native_login and collect_native_evidence deleted in this step)
- step 5: L23 contracts import: dropped NativeSpec
- step 5: deleted L27 `from .secrets import ...` (last users deleted in this step)
- step 5: deleted native_login (L85-107) with its two trailing blank lines (L108-109)
- step 5: deleted scan_tree (L198-208) with its trailing blank line
- step 5: deleted reset (L273-280) with its trailing blank line
- step 5: deleted remember, credential_bytes, read_credentials, sync_credentials, authentication_secrets (L300-333) with the trailing blank line
- step 5: deleted collect_native_evidence, model_evidence, execution_request (L435-476) with the trailing blank line
- step 5: collect_workspace: deleted L484 (scan_tree call)
- step 5: deleted observe_workspace (L489-490) with its trailing blank line
- step 5: release: deleted L631-632 (credential release) and L643-644 (vault forget)
- step 5: deleted _release_credentials (L647-661) with the blank line before it

## worker_files.py
- step 4: service_directories (L487): ('jobs', 'payment') -> ('jobs',)
- step 4: deleted the payment_requests action (L490-492)
- step 5: dispatch: deleted the reset (L465-478), file_identity (L493-501), observe_workspace (L502-503), session_evidence (L504-519) and native_evidence (L520-531) actions
- step 5: list (L588): `if relative not in {'workspace', 'grade-env'}:` -> `if relative != 'workspace':`
- step 5: no import became unused (the architecture check run against worker_sandbox reports none for this file; `re` is still used at L216)

## worker_service.py
- step 4: deleted L12 (payment gateway Popen) and L14-15 (gateway exit check)

## artifacts.py
- step 5: deleted L9 `import shutil` (unused after the deletions below)
- step 5: L14: `from .contracts import ArtifactRef, Snapshot, digest, make` -> `from .contracts import ArtifactRef, digest, make`
- step 5: deleted _snapshot (L126-150) with its two trailing blank lines
- step 5: deleted copy_artifact (L163-169) with its two trailing blank lines
- step 5: deleted snapshot (L183-187) with the two blank lines before it
