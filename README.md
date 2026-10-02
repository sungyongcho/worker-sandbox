# worker-sandbox

Run one coding-agent CLI invocation (Codex CLI, Claude Code, or any argv) inside an
account-isolated Linux sandbox, and get back its stdout, stderr, exit status and the
workspace it changed.

This is the run-only sandbox extracted from worker-benchmark-kit. The isolation code is
carried over as it was verified there; `PROVENANCE.md` maps every carried file to its source
lines and lists each edit.

## How it works

One run is one controller-side run directory and one worker-side mirror owned by a dedicated,
locked OS account (`worker-sandbox`). The controller:

1. **claims** the account: takes a global lease, refuses if any process runs as the worker or
   the worker root is not empty, uploads the workspace and seeds a fresh git repository on the
   worker side, then copies the staged login files into a fresh agent HOME;
2. **starts** a hardened transient systemd service as the worker, with a private network
   namespace whose only route out is a root-owned `slirp4netns` broker that denies every host
   address;
3. **invokes** the agent once with the prompt on stdin. The job refuses to start unless the
   mount namespace is private, `/` and `/var` are read-only, controller paths are unreadable,
   `NoNewPrivs=1` and `CapEff=0`;
4. **stops** both units, **collects** the workspace (without `.git` and generated directories),
   and **releases** the account: removes the worker run tree and verifies the root is empty.

All file transfer across the account boundary goes through a standard-library bridge that runs
as the worker user, opens every path without following links, and cannot be inspected by the
agent.

## Requirements

- Linux with systemd 250 or later, `/usr/bin/slirp4netns`, `/usr/bin/git` and `/usr/bin/python3`.
- Python 3.12 or later for the controller environment; the only runtime dependency is
  `msgspec==0.21.1`.
- `sudo` for the controller user, and root once for provisioning.
- The agent executable installed root-owned, with root-owned parent directories that are not
  group or world writable. An install under your own HOME is invisible to the worker.

## Install and test offline

```bash
python3.12 -m venv .venv
.venv/bin/python -I -m pip install . -r requirements-dev.txt
.venv/bin/python -I -B -m unittest discover -s tests
.venv/bin/python -B tools/verify_package.py --report verification/package-$(date -u +%Y%m%dT%H%M%SZ).json
```

The suite needs no root, systemd, network or agent binary; `verify_package.py` builds the wheel,
installs it into an empty environment, checks every module is byte-identical to the source and
runs the suite with network and privileged executables blocked. Reports under `verification/`
are never overwritten and are not committed.

## Provision the host (root, once)

From the source checkout:

```bash
sudo /usr/bin/python3 -I tools/setup_host.py --controller "$(id -un)"
```

This creates the `worker-sandbox` account, `/var/lib/worker-sandbox-worker` (worker-owned,
0700) and `/var/lib/worker-sandbox-controller` (controller-owned, 0700), and records them in
`/var/lib/worker-sandbox-controller/host.json`. It refuses to touch an existing account or
path. No sudoers rule, credential or agent is installed.

Install the agent root-owned, for example Claude Code:

```bash
sudo mkdir -p /usr/local/lib/worker-sandbox-claude/2.1.286
sudo cp ~/.local/share/claude/versions/2.1.286 /usr/local/lib/worker-sandbox-claude/2.1.286/claude
sudo chmod 755 /usr/local/lib/worker-sandbox-claude/2.1.286/claude
```

## Check the host

Run `sudo -v` first, in the same terminal. The doctor creates one synthetic run, executes only
Python probes (no model call), checks every isolation property live, and removes the run:

```bash
.venv/bin/python -B tools/doctor.py --report verification/doctor-$(date -u +%Y%m%dT%H%M%SZ).json --profile claude --binary /usr/local/lib/worker-sandbox-claude/2.1.286/claude
```

It passes only when every check passes: private controller files denied, another run's files
denied, host `/tmp`, `/var/tmp` and `/dev/shm` invisible, same-run tmp and IPC continuity,
delayed background output captured, internet DNS and TLS reachable, host network services
denied, a prior lease and leftover worker state both block admission, and the execution space
is removed afterwards.

## Log in (once per agent)

Each agent runs its own login into a private staging HOME under
`<control root>/credentials/<profile>/`. The tool never opens the files the agent writes; at
claim it copies only the profile's credential files into the run HOME.

```bash
worker-sandbox login --profile codex --binary /path/to/root-owned/codex
worker-sandbox login --profile claude --binary /usr/local/lib/worker-sandbox-claude/2.1.286/claude
```

Login refuses while a run holds the lease. Without `--binary`, the agent is looked up by name on
`/usr/local/bin:/usr/bin:/bin`.

## Run

The prompt is read from stdin. Run `sudo -v` first, in the same terminal.

```bash
printf 'Create a file named hello.txt containing the single word hello, then stop.\n' | worker-sandbox run --profile claude --workspace ./project --out ./sandbox-runs --binary /usr/local/lib/worker-sandbox-claude/2.1.286/claude
```

Options: `--model`, `--effort`, `--resume SESSION_ID`, `--home-dir DIR` (regular files copied
into the run HOME), `--env NAME` (pass one of the profile's credential variables through, for
example `ANTHROPIC_API_KEY`; nothing else from your environment crosses), `--no-credentials`,
`--binary-digest sha256:...`, and `-- EXTRA ARGV` appended to the agent command. There is no
timeout option.

Before the agent runs, a preflight executes the agent's login status command inside the
sandbox; if it fails, the run ends with outcome `provider_error` and the agent is never
started. The command prints one JSON document and exits 0 only when the outcome is `completed`
with exit code 0; 1 for any other outcome, 2 for a contract or host error, 130 when cancelled
with Ctrl-C.

Output layout:

```text
<out>/                        # 0700, controller-owned
  runs/<run_id>/              # 32 hex
    workspace/                # input copy at start; collected output at end
    artifacts/upstream-resolv.conf
    raw/<unit>/stdout         # the agent's own output (JSON event stream for codex and claude)
    raw/<unit>/stderr
    result.json               # run_id, profile, session_id, result, stdout, stderr
```

## Recover

If the controller dies mid-run, the lease and worker state stay in place and block the next
run. Recover that run explicitly (after `sudo -v`):

```bash
worker-sandbox recover ./sandbox-runs/runs/<run_id>
```

It refuses a run that does not hold the lease, stops the run's units, confirms no worker
process remains, and releases the account.

## Profiles

| Profile | Agent state directory | Credential files copied | Passable variables |
| --- | --- | --- | --- |
| `codex` | `CODEX_HOME=~/.codex` | `.codex/auth.json` | `OPENAI_API_KEY` |
| `claude` | `CLAUDE_CONFIG_DIR=~/.claude` | `.claude/.credentials.json`, `.claude/.claude.json` | `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` |
| `generic` | none | none | none |

Codex runs as `codex exec --json` with the `worker_sandbox` permission profile (workspace
write, network on, approvals never). Claude Code runs as
`claude -p --output-format stream-json --verbose --dangerously-skip-permissions --permission-prompts none`
with telemetry, error reporting and nonessential traffic disabled. `generic` runs the given
argv as is.

## Limitations

- Linux only. Requires systemd 250 or later (this host: 259), `sudo` for the
  controller user, root once for `setup-host`, `/usr/bin/slirp4netns`, Python 3.12
  or later for the controller environment and `/usr/bin/python3` for the worker
  bridge.
- Stage 1 runs `systemd-run` as root and drops to the worker account. There is
  no rootless mode yet; a rootless distribution is the planned final form and is
  not designed in this stage.
- One run at a time per worker account. A second `run` refuses while the lease
  exists.
- No CPU, memory, disk or time limits are imposed on the agent. The host's
  resources and the provider's limits are the only bounds.
- The workspace is copied in and out. `.git`, `.venv`, `venv`, `node_modules`
  and cache directories are not copied back; the agent works in a fresh git
  repository seeded with one commit.
- Collected stdout, stderr and workspace files are not scanned for secrets, and
  the agent HOME is discarded at release, so a refreshed login inside the run is
  lost.
- The agent's own sandbox (Codex Landlock profile, Claude Code bubblewrap) is
  not managed by this tool; Codex runs with the `worker_sandbox` permission profile
  (workspace write, network on), Claude Code with permissions skipped. The sandbox
  is the boundary.
- Only the agent executable path is checked (root-owned, not writable by group
  or others, optional digest). Its own dependencies and auto-update behavior are
  the agent's business; the Claude profile disables nonessential traffic through
  documented variables.
- Resuming across runs is not supported in stage 1. The agent's session transcripts live in
  the run HOME, which is removed at release, so the next run cannot find the session.
  `--resume` is meaningful only when the transcript is inside the run HOME (for example,
  seeded with `--home-dir`). Session preservation (the profiles' evidence directories) is a
  later-stage item.
- `setup-host` and `doctor` work only from the source checkout: `tools/` is not part of the
  installed package. Use `tools/setup_host.py` and `tools/doctor.py` directly, as shown above.
- Only the default control root `/var/lib/worker-sandbox-controller` is supported; the
  commands read `host.json` from there.
- Login files are copied as written by the agent's own login. A Claude Code login on a
  claude.ai account also brings that account's remote MCP connectors into the run.

## Repository

- `worker_sandbox/`: the package (carried modules plus `profiles.py`, `hostconfig.py` and the CLI).
- `tools/`: host provisioning, the doctor and the package verifier.
- `PROVENANCE.md`: source and edits of every carried file. `HANDOFF_QUESTIONS.md`: every
  deviation from the extraction brief and the owner's decisions.
- License: MIT (`LICENSE`).
