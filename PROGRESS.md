# Progress

Brief: <worker-benchmark>/SANDBOX_STAGE1_HANDOFF.md
Start prompt: <worker-benchmark>/SANDBOX_STAGE1_START_PROMPT.md
Repository: <worker-sandbox>
Import commit: 5ddf473

## State
- Current step: 10 Doctor
- Status: in progress
- Last commit: this commit feat: add the worker-sandbox command line
- Working tree: clean
- Offline suite: 99 passed at this commit; verify_package passed: verification/package-20261002T070822Z.json
- Next action: wait for the owner to provision the host (section 11.2) and run sudo -v; then apply section 6.11 to tools/doctor.py (verify(report_path, timeout, profile, *, check_binary)) and run it live with the owner present
- Blocked on: none

## Step log
| Step | Status | Commit | Note |
| --- | --- | --- | --- |
| 1 Scaffold | done | d6b35ec | |
| 2 Verbatim import | done | 5ddf473 | import commit holds only the copies (R1); its hash is recorded in a following docs commit |
| 3 Contracts subset | done | 8881c72 | |
| 4 Remove the payment fixture | done | ffae987 | |
| 5 Remove evidence and vault features | done | b9aa4bc | |
| 6 Parameterize | done | 17f7f42 | |
| 7 Profiles and host config | done | 934adfa | |
| 8 Tests | done | c92e17f | 6.12 verify_package edits included | |
| 9 CLI | done | this commit | setup_host 6.10 and tests 877, 902 included | |
| 10 Doctor | pending | | owner present for sudo -v |
| 11 Live acceptance | pending | | owner present |
| 12 README | pending | | |
| 13 Provenance final pass | pending | | |

## Resume checklist
1. Read this file, then the brief in full.
2. `git -C <worker-sandbox> status --short` and `git -C <worker-sandbox> log --oneline -20`.
3. Every step marked done must have its commit hash in the log; if not, the
   record is wrong: fix the record, never redo the step blindly.
4. If the working tree is dirty, diff it against the enumerated edits of the
   current step only; finish or revert that step, nothing else.
5. If "Offline suite" says not run since the last commit, run it.
6. Continue at "Next action".
