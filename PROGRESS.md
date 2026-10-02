# Progress

Brief: <worker-benchmark>/SANDBOX_STAGE1_HANDOFF.md
Start prompt: <worker-benchmark>/SANDBOX_STAGE1_START_PROMPT.md
Repository: <worker-sandbox>
Import commit: 5ddf473

## State
- Current step: 7 Profiles and host config
- Status: in progress
- Last commit: this commit refactor: parameterize the account, roots and agent home
- Working tree: clean
- Offline suite: 2 passed, 2 import errors (tests still import benchkit until step 8) at this commit; run from the repository root with .venv/bin/python -I -B -m unittest discover -s tests
- Next action: write profiles.py and hostconfig.py, apply the 6.7 __init__ signature, native_home deletion, seed_native_state body and _job environment hunks, delete the credentials import, then delete exactly the unused imports the architecture check names
- Blocked on: none

## Step log
| Step | Status | Commit | Note |
| --- | --- | --- | --- |
| 1 Scaffold | done | d6b35ec | |
| 2 Verbatim import | done | 5ddf473 | import commit holds only the copies (R1); its hash is recorded in a following docs commit |
| 3 Contracts subset | done | 8881c72 | |
| 4 Remove the payment fixture | done | ffae987 | |
| 5 Remove evidence and vault features | done | b9aa4bc | |
| 6 Parameterize | done | this commit | |
| 7 Profiles and host config | in progress | | |
| 8 Tests | pending | | |
| 9 CLI | pending | | |
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
