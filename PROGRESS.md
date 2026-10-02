# Progress

Brief: <worker-benchmark>/SANDBOX_STAGE1_HANDOFF.md
Start prompt: <worker-benchmark>/SANDBOX_STAGE1_START_PROMPT.md
Repository: <worker-sandbox>
Import commit: 5ddf473

## State
- Current step: 9 CLI
- Status: in progress
- Last commit: this commit test: port the sandbox tests
- Working tree: clean
- Offline suite: 80 passed at this commit (pip install the repo into .venv first; -I imports the installed package); verify_package passed: verification/package-20261002T070216Z.json
- Next action: write worker_sandbox/__main__.py (section 6.9), parameterize tools/setup_host.py (6.10), add tests/test_cli.py, re-add tests 877 and 902 adapted to login, run the suite and verify_package
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
| 8 Tests | done | this commit | 6.12 verify_package edits included | |
| 9 CLI | in progress | | |
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
