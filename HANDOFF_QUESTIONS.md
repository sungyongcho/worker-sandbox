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
