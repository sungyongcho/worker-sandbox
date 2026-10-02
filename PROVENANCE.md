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
| tools/setup_host.py | tools/setup_host.py | 1-59 | see "setup_host.py" below |
| tools/doctor.py | tools/verify_host.py | 1-263 | none yet |
| tools/verify_package.py | tools/verify_package.py | 1-136 | see "verify_package.py" below |
| tests/test_runtime.py | tests/test_runtime.py | 1-1192 | see "test_runtime.py" below |
| tests/test_bulk_transfer.py | tests/test_bulk_transfer.py | 1-256 | see "test_bulk_transfer.py" below |
| tests/test_architecture.py | tests/test_architecture.py | 1-68 | see "test_architecture.py" below |
| tests/fixtures.py | tests/fixtures.py | 1-236 | see "fixtures.py" below |
| worker_sandbox/__init__.py | new | | |
| worker_sandbox/profiles.py | new (Codex facts from benchkit/adapters.py L12, L96-99, L177-184, L265-267 and runtime.py L69-73) | | |
| worker_sandbox/hostconfig.py | new | | |
| tests/test_profiles.py | new | | |
| tests/test_cli.py | new (preflight idea from tests/test_native_preparation.py L79) | | |
| worker_sandbox/__main__.py | new (modeled on benchkit/__main__.py: argparse, dispatch, exit codes 0/1/2/130) | | |
| Makefile | Makefile | 1-9 | none |
| requirements-dev.txt | requirements-dev.txt | 2 | step 8: dropped jsonschema==4.26.0 (no schema check is carried; section 5) |
| .gitignore | .gitignore | 1-13 | added /verification/, /runs/, /sandbox-runs/ |
| pyproject.toml | pyproject.toml | 1-20 | adapted: name, description, script, package; package data dropped |

## contracts.py
- step 3: kept only the section 6.1 subset, copied from the listed source lines in source order
- step 3: dropped the datetime import, FORMAT, Positive and every type and helper not listed in section 6.1
- step 3: Interval.kind Literal['active', 'native', 'jev', 'setup'] -> Literal['native', 'setup']
- step 3: make: deleted the two-line `if issubclass(cls, Document):` block (L577-579 with its body)
- step 3: blank lines between the joined ranges normalized to two
- step 6: RuntimeSpec.account: Literal["food-delivery"] -> Annotated[str, msgspec.Meta(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")] = "worker-sandbox"
- step 6: RuntimeSpec.worker_root -> Text = "/var/lib/worker-sandbox-worker"; control_root -> Text = "/var/lib/worker-sandbox-controller"; python -> Text = "/usr/bin/python3"
- step 6: RuntimeSpec: deleted workspace_storage (L89)
- step 6: RuntimeSpec: added __post_init__ refusing non-absolute or NUL-containing worker_root, control_root and python, modeled on NativeSpec.__post_init__ (L102-106)

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
- step 6: __init__ (L112): added keyword `path_prefix: str | None = None`; inserted `self.spec_path_prefix = path_prefix` after L127 (section 6.7, _job)
- step 6: inspect L214: "food-delivery account is not provisioned" -> f"{self.spec.account} account is not provisioned"
- step 6: inspect L230: deleted 'disk_hard_quota': False from the returned dict
- step 6: verify_model L233-234, L243: takes (binary: str, binary_digest: str | None); Path(model.binary) -> Path(binary); the digest comparison runs only when binary_digest is not None
- step 6: claim L252: 'food-delivery is leased by another run; ...' -> f'{self.spec.account} is leased by another run; ...'
- step 6: start_run L385, _job L535 and L537: homes/swe -> home
- step 7: added `from .profiles import AgentProfile`
- step 7: deleted L24 `from .credentials import ...` (last users in seed_native_state replaced in this step)
- step 7: deleted native_home (L69-73) with its two trailing blank lines: replaced by AgentProfile.home_environment
- step 7: __init__ signature -> (spec, run, agent: AgentProfile, *, authentication=True, home_dir: Path | None = None, path_prefix: str | None = None), wrapped over two lines
- step 7: __init__ L127: self.profile = ... -> self.agent = agent and self.home_dir = Path(home_dir) if home_dir is not None else None
- step 7: seed_native_state (L282-298): body and docstring replaced with the section 6.7 text verbatim; name kept
- step 7: _job L539-540: environment from control_environment, agent.home_environment, agent.environment and TMPDIR; PATH prefixed only when path_prefix is set
- step 7: _job: inserted the credential_env passthrough after the environment is built, before `job = {...}`
- step 7: deleted exactly the imports the architecture check named as unused: threading (L16), uuid (L18), reference_file and safe_open (L22)

## worker_files.py
- step 4: service_directories (L487): ('jobs', 'payment') -> ('jobs',)
- step 4: deleted the payment_requests action (L490-492)
- step 5: dispatch: deleted the reset (L465-478), file_identity (L493-501), observe_workspace (L502-503), session_evidence (L504-519) and native_evidence (L520-531) actions
- step 5: list (L588): `if relative not in {'workspace', 'grade-env'}:` -> `if relative != 'workspace':`
- step 6: RUN_DIRECTORIES (L18): ('workspace', 'homes', 'homes/swe', 'tmp') -> ('workspace', 'home', 'tmp')
- step 5: no import became unused (the architecture check run against worker_sandbox reports none for this file; `re` is still used at L216)

## worker_service.py
- step 4: deleted L12 (payment gateway Popen) and L14-15 (gateway exit check)

## artifacts.py
- step 5: deleted L9 `import shutil` (unused after the deletions below)
- step 5: L14: `from .contracts import ArtifactRef, Snapshot, digest, make` -> `from .contracts import ArtifactRef, digest, make`
- step 5: deleted _snapshot (L126-150) with its two trailing blank lines
- step 5: deleted copy_artifact (L163-169) with its two trailing blank lines
- step 5: deleted snapshot (L183-187) with the two blank lines before it

## test_runtime.py
- step 8: imports: benchkit -> worker_sandbox; dropped the credentials and adapters imports and native_login; added the profiles import; fixtures import reduced to codex_auth
- step 8: every patch target 'benchkit.<module>.' -> 'worker_sandbox.<module>.'
- step 8: dropped (section 10.1): 66 reset_keeps..., 110 native_archive_rejects_linked..., 206 supervisor_reports_gateway_exit..., 279 changed_run_profile..., 564, 595, 622, 629 (native archive), 685 credentials_cross_conditions..., 731, 759, 769, 803, 825, 845, 859 (vault, refresh, mismatch, scan), 1012, 1025, 1036 (secret scan, archive), 1093 reset_requires...
- step 8: 877 and 902 (login) removed here and re-added adapted with the login command in step 9
- step 8: setUp: deleted `self.model = WORKER` (ModelSpec is not carried); NativeRuntime(c.RuntimeSpec(), self.run) -> NativeRuntime(c.RuntimeSpec(), self.run, generic('/usr/bin/native'))
- step 8: request(), home_files(): homes/swe -> home; claim(): seed import path
- step 8: 102 file_listing_never_exposes_a_native_home: homes/swe -> home
- step 8: 255 existing_lease_preserves_native_credentials_without_reseeding: codex profile with a staged .codex/auth.json from fixtures.codex_auth() instead of the vault; the no-reseed assertion stays
- step 8: 264 custom_profile_is_seeded_into_codex_home_only_and_verified -> home_dir_is_seeded_into_codex_home_only_and_verified: home_dir files land under home/ and read back identical, beside the staged auth.json
- step 8: 288 grader_runtime_does_not_seed_native_credentials: staged codex file instead of the vault; authentication=False still seeds nothing
- step 8: 517: User=food-delivery -> User=worker-sandbox
- step 8: 532 setup_resolver_survives_hidden_host_target_and_is_archived: deleted the five collect_native_evidence lines (L558-562); name kept
- step 8: 787 implementer_receives_only_declared_environment: claude profile; exact expected environment (no PAYMENT_PROVIDER_URL, HOME at .../home, profile environment and CLAUDE_CONFIG_DIR, nothing from os.environ)
- step 8: 925 invalid_binary_stops_login_before_any_subprocess: calls NativeRuntime.verify_model(binary, digest) directly instead of native_login; same stat cases; also checks a None digest returns the identity
- step 8: 966 private_environment_and_role_home: codex profile set first; PATH /usr/local/bin:/usr/bin:/bin; homes/swe -> home
- step 8: 994 bulk_bridge_count_does_not_grow_with_file_count: deleted the native-evidence block (L1003-1009)
- step 8: 1069, 1075: unit User food-delivery -> worker-sandbox
- step 8: 1150: deleted the handle_payments patch line (L1160)
- step 9: 877 login_imports_a_validated_credential_from_a_private_staging_home re-added, adapted to worker_sandbox.__main__.login: a fake login argv writes .codex/auth.json into credentials/codex; staging and its parent are 0700; status argv runs second; a claim then seeds exactly the credential file
- step 9: 902 login_failures_leave_the_vault_unchanged re-added, adapted: a failing login argv leaves the staged files unchanged; login under a lease runs no subprocess

## test_bulk_transfer.py
- step 8: L15 benchkit -> worker_sandbox. Test 64 never calls native_evidence, so no assertion was removed

## test_architecture.py
- step 8: ROOT and the three package-name literals: benchkit -> worker_sandbox

## fixtures.py
- step 8: kept the docstring, json and Path imports, codex_auth (L16-20) and FakeRuntime (L157-236)
- step 8: imports reduced to worker_sandbox contracts and tree_manifest
- step 8: FakeRuntime: dropped sync_credentials, read_credentials, collect_native_evidence, observe_workspace, model_evidence, execution_request
- step 8: FakeRuntime.verify_model(self, model) -> verify_model(self, binary, binary_digest), returning binary_digest (the NativeSpec argument is not carried)

## verify_package.py
- step 8: IMPORT_CHECK and OFFLINE_TESTS: benchkit -> worker_sandbox; distribution name worker-benchmark-kit -> worker-sandbox
- step 8: offline guard: blocked executables gain "claude" beside "codex"
- step 8: stripped environment: JEV_API_KEY -> ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN
- step 8: temporary directory prefix benchkit-verification- -> worker-sandbox-verification-
- step 8: no schema export check exists in the carried file; nothing dropped

## setup_host.py
- step 9: L16-18 constants: new defaults worker-sandbox, /var/lib/worker-sandbox-worker, /var/lib/worker-sandbox-controller; added PYTHON = '/usr/bin/python3'
- step 9: prepare(controller) -> prepare(controller, account=ACCOUNT, worker=WORKER, control=CONTROL); every use of the constants inside prepare uses the parameters; the local `worker` (the account entry) renamed `worker_account` because `worker` is now the root parameter
- step 9: added `import json`, host_document and write_host: host.json in the canonical bytes of contracts.dumps(RuntimeSpec), O_EXCL 0600, owned by the controller (see HANDOFF_QUESTIONS)
- step 9: main gains --account, --worker-root, --control-root and --python, then calls write_host after prepare. The useradd argv and every refusal are unchanged
