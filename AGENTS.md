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
- PROGRESS.md is the single record of where work stands. A session that starts
  or resumes reads it before anything else and updates it in every commit.
