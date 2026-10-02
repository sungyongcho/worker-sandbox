# Progress

Brief: <worker-benchmark>/SANDBOX_STAGE1_HANDOFF.md
Start prompt: <worker-benchmark>/SANDBOX_STAGE1_START_PROMPT.md
Repository: <worker-sandbox>
Import commit: 5ddf473

## State
- Current step: 11 Live acceptance
- Status: in progress
- Last commit: 13d7156 fix(profiles): stage the claude global config from its config directory as login writes it
- Working tree: clean
- Offline suite: 101 passed at 13d7156; verify_package passed: verification/package-20261002T083950Z.json
- Next action: owner reruns the codex hello.txt run with --model gpt-6-sol (section 11.4 fallback; argument only); judge result.json, worker root and bk-* units
- Blocked on: owner: interactive login and sudo -v for the runs (run through the terminal panel, never `!`)

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
| 9 CLI | done | b43a79e | setup_host 6.10 and tests 877, 902 included | |
| 10 Doctor | done | 700f26d | passed live: verification/doctor-20261002T083240Z.json (19 checks; worker root empty; no bk-* unit); attempts 1-3 refused by sudo -n in the agent shell |
| 11 Live acceptance | in progress | | codex login passed (status_exit_code 0, .codex/auth.json 0600 staged; codex prints status on stderr, so the JSON status is empty); claude login passed (loggedIn true, status_exit_code 0; .claude/.credentials.json and .claude/.claude.json staged 0600, both under CLAUDE_CONFIG_DIR); codex run 1 (verification/acceptance/runs/871371c8959b42f9b7299d4330871fc9) exit 1 provider_error: gpt-6.1-sol not supported for ChatGPT accounts on codex 0.157.0; sandbox path complete (preflight, session id, collection, release; worker root empty, no bk-* unit) |
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
