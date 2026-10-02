# Working agreement

- This repository is the standalone sandbox extracted from worker-benchmark-kit.
  PROVENANCE.md maps every carried file to its source lines. Carried code is
  changed only with a recorded reason in PROVENANCE.md.
- Keep the package minimal: standard library plus the pinned msgspec. No
  containers, no bubblewrap, no async, no resource limits on agent execution.
- Never commit credentials, staged login files, run outputs or verification
  reports. `verification/` and `sandbox-runs/` are ignored.
- Live checks (`doctor`, `run`) need `sudo -v` first and a provisioned host.
  Do not run them without the owner present.
- Commits are one-line Conventional Commits in English with no attribution
  trailers. Code, comments and documentation are in English.
- docs/history/ holds the extraction record: docs/history/PROGRESS.md is where
  the staged work stands, docs/history/HANDOFF_QUESTIONS.md every deviation and
  owner decision. A session that resumes staged work reads PROGRESS.md first.
