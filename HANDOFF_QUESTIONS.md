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
