# Questions and deviations

## 2026-10-02 .gitignore:1
Expected: "`.gitignore`: the kit's, plus `/verification/` and `/runs/`" (section 5).
Observed: the AGENTS.md text in section 12.3 also states that `sandbox-runs/` is
ignored, and `sandbox-runs` is the default `--out` of `run` (section 6.9).
Did instead: added `/sandbox-runs/` as a third line so AGENTS.md is true.
Shown by: `git -C <worker-sandbox> check-ignore -v sandbox-runs/`.

## 2026-10-02 PROVENANCE.md:1
Expected: `tools/setup_host.py` 60 lines and `tools/verify_host.py` 262 lines (section 4.1).
Observed: `wc -l` reports 59 and 263 at 8bb76be.
Did instead: nothing; recorded the observed counts in PROVENANCE.md. Line references
in sections 6.10 and 6.11 are checked against the source when those steps run.

## 2026-10-02 worker_sandbox/contracts.py:54
Expected: section 6.1 lists the RuntimeSpec field edits, and section 12 step 3 says
"Cut contracts to the subset: section 6.1", while step 6 says "Parameterize: `RuntimeSpec`, ...".
Observed: both steps claim the RuntimeSpec edit.
Did instead: step 3 cut the subset and made the Interval and make edits; the RuntimeSpec
field edits, the workspace_storage deletion and the new __post_init__ are made in step 6
together with the inspect and claim messages that depend on them. The final diff is the same.

## 2026-10-02 worker_sandbox/contracts.py:54
Expected: "Model it on `NativeSpec.__post_init__` (L44-48)" (section 6.1).
Observed: at 8bb76be, NativeSpec.__post_init__ is at L102-106; L44-48 is Halt.__init__.
Did instead: used L102-106 as the model (applied in step 6).

## 2026-10-02 worker_sandbox/runtime.py:358
Expected: "Docstring L359-362: delete the second sentence about the payment ledger; keep the first" (section 6.7).
Observed: the docstring opens on L358; L359 is blank, L360-361 hold the second sentence, L362 closes it.
Did instead: kept L358 with closing quotes added, deleted L359-362. Same meaning, one line earlier.

## 2026-10-02 worker_sandbox/runtime.py:109
Expected: rpc is "unchanged" (section 6.7), and the native_evidence bridge action is deleted (section 6.4).
Observed: rpc keeps `timeout=None if action in {'list', 'file_refs', 'native_evidence'} else 30`;
the 'native_evidence' member now names an action that no longer exists. It is harmless.
Did instead: nothing; left rpc byte-for-byte as R2 requires. The owner may approve dropping the member.

## 2026-10-02 worker_sandbox/runtime.py:21
Expected: section 6.7 lists the runtime import deletions; section 12 splits the work over steps 4, 5 and 7.
Observed: the brief does not say in which step each import line goes.
Did instead: each import line is deleted in the step that removes its last user: payment_gateway and
payment_fixture in step 4; adapters, secrets and NativeSpec in step 5; credentials in step 7, because
seed_native_state still uses AUTH_FILE and CredentialVault until step 7 replaces its body. Between
steps 5 and 7, seed_native_state calls the deleted remember(); the package does not run before step 8.

## 2026-10-02 worker_sandbox/worker_files.py:9
Expected: "`re` is used by `payment_requests` only; check with the architecture test, do not guess" (section 6.4).
Observed: `re` is also used in push_tree's digest check (L216 at 8bb76be).
Did instead: kept `import re`.
