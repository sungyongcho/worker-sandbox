# Progress

Brief: <worker-benchmark>/SANDBOX_STAGE1_HANDOFF.md
Start prompt: <worker-benchmark>/SANDBOX_STAGE1_START_PROMPT.md
Repository: <worker-sandbox>
Import commit: 5ddf473

## State
- Current step: 13 Provenance final pass (stage 1 complete)
- Status: done
- Last commit: f682ecc docs: complete the provenance record
- Working tree: clean
- Offline suite: 101 passed at f682ecc; verify_package passed: verification/package-20261002T083950Z.json
- Next action: none in stage 1; the owner chooses the license, adds a remote, and corrects brief sections 7.2, 9 and 11.4 in worker-benchmark
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
| 8 Tests | done | c92e17f | 6.12 verify_package edits included |
| 9 CLI | done | b43a79e | setup_host 6.10 and tests 877, 902 included |
| 10 Doctor | done | 700f26d | passed live: verification/doctor-20261002T083240Z.json (19 checks; worker root empty; no bk-* unit); attempts 1-3 refused by sudo -n in the agent shell |
| 11 Live acceptance | done | 6194637, 13d7156 (fixes) | codex login passed (status_exit_code 0, .codex/auth.json 0600 staged; codex prints status on stderr, so the JSON status is empty); claude login passed (loggedIn true, status_exit_code 0; .claude/.credentials.json and .claude/.claude.json staged 0600, both under CLAUDE_CONFIG_DIR); codex run 1 (verification/acceptance/runs/871371c8959b42f9b7299d4330871fc9) exit 1 provider_error: gpt-6.1-sol not supported for ChatGPT accounts on codex 0.157.0; sandbox path complete (preflight, session id, collection, release; worker root empty, no bk-* unit); codex run 2 with --model gpt-6-sol passed (runs/f6b0d91a840e400f9e95ced2a20bdf41: exit 0, completed, session 01a0fbc9-0a67-7820-a255-bbb4d756948c, hello.txt = hello, worker root empty, no bk-* unit); claude run passed (runs/6869265f80344f1a9f135dc16c9693d1: exit 0, completed, session fa7d3db4-bf4d-49fd-bb16-d37f54e0d077, hello.txt = hello, stream-json system/init..result, root-owned copy runs, worker root empty, no bk-* unit); claude resume failed: No conversation found (runs/fad51dfa6fed448a87d36aaa1cfd9e7f; HOME discarded at release); owner closed both resume items as not supported by design, codex resume not run |
| 12 README | done | cd018d1 | section 13 verbatim plus the owner-decided limits |
| 13 Provenance final pass | done | f682ecc | every tracked file has a row; final audit maps all hunks |

## Resume checklist
1. Read this file, then the brief in full.
2. `git -C <worker-sandbox> status --short` and `git -C <worker-sandbox> log --oneline -20`.
3. Every step marked done must have its commit hash in the log; if not, the
   record is wrong: fix the record, never redo the step blindly.
4. If the working tree is dirty, diff it against the enumerated edits of the
   current step only; finish or revert that step, nothing else.
5. If "Offline suite" says not run since the last commit, run it.
6. Continue at "Next action".

## Stage 2

Brief: <worker-benchmark>/SANDBOX_STAGE2_HANDOFF.md
Base commit: bd84174

### State
- Current step: 9 Provenance and progress (stage 2 complete)
- Status: done
- Last commit: 4a9173c docs: complete the stage 2 record
- Working tree: clean
- Offline suite: 125 passed; verify_package passed: verification/package-20261002T094749Z.json
- Next action: none in stage 2; owner may rerun the root-mode doctor and Claude acceptance with WORKER_SANDBOX_MODE=root and sudo
- Blocked on: none

### Step log
| Step | Status | Commit | Note |
| --- | --- | --- | --- |
| 1 Record the start | done | 1782c85 | owner approved starting stage 2 and running the doctor and live acceptance directly |
| 2 Reproduce M8 | done | 8603228 | m8 and runtime variants passed; /run needs the resolver bound at the symlink target | |
| 3 Contracts | done | 7ae6eeb | stage 1 host.json byte test now compares the decoded spec | |
| 4 Rootless runtime | done | bdcf8bc | worker root moved out of HOME; bridge without --mount-proc; setup-job launcher | |
| 5 CLI and provisioning | done | dc40420 | host provisioned rootless; rootless host.json preferred when present | |
| 6 Doctor mode | done | 2ababe6, 31bffdf (fix) | attempt 2 passed: verification/doctor-rootless-20261002T093518Z.json; attempt 1 failed on the worker root parent mode | |
| 7 Live acceptance | done | 1473ad0, ad6c33a, ee1af75, fdf3871 | codex runs/4d0a5d8c9e3b486890cc6cefc7b1a1b3 and claude runs/8c0a52ba3c514b6aba074ffb72937934 passed; codex needed its own sandbox off (owner decision); --strict-mcp-config added after 7.4 |
| 8 README | done | 1db20c0 | three stage 1 limitation lines updated | |
| 9 Provenance and progress | done | 4a9173c | 7.5 diff empty | |
