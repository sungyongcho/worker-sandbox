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

## 2026-10-02 worker_sandbox/runtime.py:109
Expected: rpc is "unchanged" (section 6.7), and the native_evidence bridge action is deleted (section 6.4).
Observed: rpc keeps `timeout=None if action in {'list', 'file_refs', 'native_evidence'} else 30`;
the 'native_evidence' member now names an action that no longer exists. It is harmless.
Did instead: nothing; left rpc byte-for-byte as R2 requires. The owner may approve dropping the member.

## 2026-10-02 worker_sandbox/runtime.py:21
Expected: section 6.7 lists the runtime import deletions; section 12 splits the work over steps 4, 5 and 7.
Observed: the brief does not say in which step each import line goes.
Did instead: each import line is deleted in the step that removes its last user: payment_gateway and
payment_fixture in step 4; adapters, secrets and NativeSpec in step 5; credentials in step 7, because
seed_native_state still uses AUTH_FILE and CredentialVault until step 7 replaces its body. Between
steps 5 and 7, seed_native_state calls the deleted remember(); the package does not run before step 8.

## 2026-10-02 worker_sandbox/worker_files.py:9
Expected: "`re` is used by `payment_requests` only; check with the architecture test, do not guess" (section 6.4).
Observed: `re` is also used in push_tree's digest check (L216 at 8bb76be).
Did instead: kept `import re`.

## 2026-10-02 worker_sandbox/runtime.py:1
Expected: "After these edits the file should be roughly 420 lines" (section 6.7).
Observed: 472 lines after step 7. Count of the enumerated hunks: 661 source lines, 194 deleted
(imports 6, native_home 7, native_login 25, __init__ 4, scan_tree 12, reset 9, the five credential
methods 35, start_run 7, payment handlers 18, evidence methods 43, collect_workspace 1,
observe_workspace 3, _job 2, _observe 2, release 4, _release_credentials 16), 5 net added
(profiles import, __init__ lines, _job environment and passthrough, minus 3 in seed_native_state).
Did instead: nothing; the estimate in the brief appears to be low. `git diff 5ddf473 -- worker_sandbox/runtime.py`
shows only the enumerated hunks.

## 2026-10-02 worker_sandbox/runtime.py:373
Expected: the _job passthrough copies every `agent.credential_env` name present in os.environ, and
"--env NAME passes through at most the names in profile.credential_env" (sections 6.7, 6.9, 8).
Observed: if the runtime alone decides, a key exported in the owner's shell crosses into the job even
without `--env`.
Did instead: runtime.py follows section 6.7 exactly. In step 9 the CLI narrows the profile it hands to
NativeRuntime to the names given with `--env` (msgspec.structs.replace on credential_env), so nothing
crosses unless `--env` names it.

## 2026-10-02 worker_sandbox/hostconfig.py:13
Expected: setup-host accepts `--control-root`; doctor, login, run and recover read host.json (section 6.9).
Observed: the brief gives the other commands no way to learn a non-default control root.
Did instead: hostconfig.read() takes an optional control_root and defaults to /var/lib/worker-sandbox-controller.
A host provisioned with a custom --control-root needs a way to point the CLI at it; owner to decide
(for example a --control-root option on every command).

## 2026-10-02 tests/test_runtime.py:877
Expected: tests 877 and 902 are adapted "to `worker-sandbox login`" (section 10.1) in step 8, and the suite passes before step 9.
Observed: the login command is written in step 9.
Did instead: step 8 removes both tests; step 9 re-adds them, adapted and under their names, in the same commit as the login command.

## 2026-10-02 tests/test_runtime.py:1
Expected: "about 46 of the 70 NativeRuntimeTests" (section 10.1).
Observed: the disposition table keeps or adapts 43 NativeRuntimeTests (41 after step 8, 43 after step 9).
Did instead: followed the table.

## 2026-10-02 tests/test_runtime.py:1
Expected: kept tests change only where they reference removed features (R7).
Observed: tests 517, 1069 and 1075 assert the unit owner `food-delivery`, test 532 ends with a collect_native_evidence block,
test 994 ends with one, and test 1150 patches handle_payments. All of these are removed or renamed features.
Did instead: renamed the owner to worker-sandbox and deleted only those lines; names kept. Sentinel names that are not
features (`@benchkit-` socket label in 386, BENCHKIT_* and JEV_API_KEY environment names that must not be forwarded) are left as they are.

## 2026-10-02 tools/verify_package.py:54
Expected: "Keep the offline guard that blocks network, `sudo`, `systemctl`, `systemd-run` and the agent binaries" (section 6.12).
Observed: the carried guard blocks only `codex`; the stripped environment lists JEV_API_KEY.
Did instead: added `claude` to the blocked executables and replaced JEV_API_KEY with the Claude credential variables.

## 2026-10-02 tools/setup_host.py:53
Expected: "After `prepare`, write `host.json` through `hostconfig.write`" (6.10), and the owner provisions with
`sudo /usr/bin/python3 -I tools/setup_host.py --controller ...` (11.2).
Observed: hostconfig imports contracts, which needs msgspec; root's /usr/bin/python3 has no msgspec, and -I keeps the
checkout off sys.path. Both instructions cannot hold together.
Did instead: setup_host.py stays standard-library only and writes host.json itself (host_document, write_host) in the
exact bytes contracts.dumps produces. tests/test_cli.py HostConfigTests pins setup_host.host_document to
contracts.dumps and reads the file back with hostconfig.read; hostconfig.write remains and writes the same bytes.

## 2026-10-02 worker_sandbox/__main__.py:28
Expected: `worker-sandbox setup-host` runs `tools/setup_host.prepare`; `worker-sandbox doctor` runs `tools/doctor.verify` (6.9).
Observed: pyproject packages only worker_sandbox (section 5: no package data), so an installed wheel has no tools/.
Did instead: both commands load tools/<name>.py from the source checkout beside the package and refuse with a clear
message when it is absent. They work from the checkout (`python -m worker_sandbox ...` run in the repository root,
or an editable install); the direct `tools/setup_host.py` and `tools/doctor.py` commands of section 11 are unaffected.
Owner to decide whether tools should move into the package later.

## 2026-10-02 worker_sandbox/__main__.py:39
Expected: `worker-sandbox login --profile codex|claude` (6.9, 11.4) builds `profiles.<P>(binary)`.
Observed: login has no binary source, and run's --binary is optional.
Did instead: login and run take an optional --binary; without it the agent is looked up by name on the worker PATH
/usr/local/bin:/usr/bin:/bin. The section 11.4 login commands therefore need --binary for the Claude copy under
/usr/local/lib/worker-sandbox-claude/... Login verifies the binary with verify_model(binary, None) first, as native_login did.

## 2026-10-02 worker_sandbox/__main__.py:121
Expected: `argv=profile.command(...)+extra` (6.9 step 7) and `command(..., extra)` returns argv "+ extra" (6.8).
Observed: following both appends EXTRA ARGV twice.
Did instead: extra is passed once, through command(extra=...). tests/test_cli.py test_extra_argv_is_appended_once.

## 2026-10-02 worker_sandbox/__main__.py:1
Expected: `__main__.py` "≤ 250 lines" (4.2 size guide).
Observed: 257 lines.
Did instead: nothing; the overrun is the doctor and setup-host loaders described above.

## 2026-10-02 worker_sandbox/__main__.py:104
Expected: a failed preflight stops "with outcome `provider_error`" (6.9 step 5); result.json has run_id, profile,
session_id, result, stdout, stderr (6.9 step 9, section 9).
Did instead: result.json carries the preflight RuntimeResult with outcome set to provider_error (error text kept, or
"login status exited with N"), session_id null, and stdout/stderr pointing at the status job's logs. No extra key was added.

# Owner decisions (2026-10-02)
- tools/setup_host.py:53: approved; setup_host stays standard-library only and writes host.json itself.
- worker_sandbox/__main__.py:28: approved; tools/ is checkout-only, stated in the README; moving it into the package is a stage 2 item.
- worker_sandbox/__main__.py:39: approved; the section 11.4 login commands take --binary.
- worker_sandbox/hostconfig.py:13: stage 1 supports only the default control root; stated in the README.
- worker_sandbox/runtime.py:109: the 'native_evidence' member of rpc stays as it is.
- Every other entry above: approved.
- Host provisioned by the owner: account worker-sandbox (uid 994), /var/lib/worker-sandbox-worker,
  /var/lib/worker-sandbox-controller/host.json, /usr/local/lib/worker-sandbox-claude/2.1.286/claude (root-owned;
  `--version` printed 2.1.286 from an empty HOME).
- Live steps 10 and 11: this session has no sudo. The agent commits code and gives one exact command per line; the
  owner runs it after `sudo -v` and reports the report path; the agent reads and judges it, then gives the next command.

## 2026-10-02 tools/doctor.py:1
Expected: 6.11 lists the doctor edits; "Everything else stays".
Observed: after the listed deletions, the module docstring still described the Jev endpoint, the probe names still
contained `grader` and `benchkit` (the Appendix A grep flags both), and the residue check builds a second NativeRuntime
whose signature now requires a profile.
Did instead: rewrote the two docstring sentences, renamed the three literals (private-controller-canary,
worker-sandbox-write-probe, worker-sandbox-host-check-) and passed the profile to the second NativeRuntime.
Every check, its order and its pass criterion are unchanged. tests/test_cli.py DoctorTests imports the module offline.

## 2026-10-02 worker_sandbox/__main__.py:69
Expected: login runs "exactly like `native_login` L94-100" (6.9).
Observed: native_login created the staging `.codex` directory first (runtime.py L95 at 8bb76be); the step 9 login did not,
so CODEX_HOME pointed at a missing directory. Found by review before live acceptance, not by a failing run.
Did instead: login creates `<staging>/<config_subdir>` with mode 0700 when the profile has one. Test 877 now asserts the
directory exists with 0700 when the agent starts.

## 2026-10-02 worker_sandbox/profiles.py:105
Brief 7.2 correction, measured at login. Expected: claude `credential_files` = ('.claude/.credentials.json', '.claude.json'),
because "Whether it moves under CLAUDE_CONFIG_DIR is not stated" (7.2).
Observed: `worker-sandbox login --profile claude` with claude 2.1.286 staged `.claude/.credentials.json` and
`.claude/.claude.json` (both 0600) under CLAUDE_CONFIG_DIR; no `.claude.json` appeared directly in HOME. The second
entry would always be skipped as missing.
Did instead (owner approved): credential_files = ('.claude/.credentials.json', '.claude/.claude.json'); tests/test_profiles.py
pins the credential files of every profile.

## 2026-10-02 verification/acceptance/runs/6869265f80344f1a9f135dc16c9693d1
Observation from the claude acceptance run (passed): the final message says the claude.ai Exa, Slack and Smartling
connectors need authorization, so the account's remote MCP connectors load inside the sandbox from the staged login.
Did instead: nothing; stage 1 copies the login as the brief says. Owner to decide in stage 2 whether runs disable
account connectors (for example through settings or an environment variable).

## 2026-10-02 section 11.4 resume
Expected: `run --resume SESSION` in a new run continues the session (11.4), with a new run, claim and service (section 9).
Observed before running it: section 8 discards the agent HOME at release, and both agents keep session transcripts
there (.claude/projects, .codex/sessions). A resumed run starts from a fresh HOME, so the agent may not find the session.
Did instead: ran the resume as written to collect evidence. Result (verification/acceptance/runs/fad51dfa6fed448a87d36aaa1cfd9e7f):
exit 1, provider_error in 0.5 s, no model call; stream `result` error_during_execution with
"No conversation found with session ID: fa7d3db4-bf4d-49fd-bb16-d37f54e0d077"; hello.txt unchanged; worker root empty,
no bk-* unit, no lease. The sandbox path worked; the session transcript left with the discarded HOME.
Carrying sessions across runs needs either reading the worker HOME (the bridge `list` refuses any tree but `workspace`,
6.4) or keeping one service across CLI calls (section 9 defers it). Both are outside stage 1. Blocked; owner decides.

## 2026-10-02 brief sections 9 and 11.4 (owner decision)
Brief contradiction: section 9 makes a resume a new run with a new claim and a new service, and 11.4 asks for resume
acceptance, but section 8 removes the agent HOME, where both agents keep session transcripts, at every release.
Owner decision: run-to-run resume is not supported in stage 1. No code change. The two 11.4 resume items are closed as
"not supported by design, not run" (the claude resume above is the evidence; the codex resume was not run). The argv
position of --resume is covered offline by tests/test_profiles.py. The README states the limitation with its cause.
Session preservation (evidence_directories) is a later-stage item; the brief is corrected in worker-benchmark after stage 1.

# Stage 2

## 2026-10-02 tools/probe_rootless.py (M8 reproduced)
M8 reproduced on this host without root: verification/probe-rootless-m8-20261002T092217Z.json (Appendix A options
exactly) and verification/probe-rootless-runtime-20261002T092238Z.json (plus the read-only /run tmpfs of section 4.1),
both passed: inner uid 1000, gid 1000, no groups, CapEff 0, NoNewPrivs 1, /, /var and /run read-only, HOME and a host
/tmp canary hidden, tap0 present, DNS and TLS to api.anthropic.com, every host IPv4 address, 10.0.2.2 and 127.0.0.1
time out, host IPv6 addresses unreachable (slirp4netns has no IPv6 route), `nft flush ruleset` refused. The 5.3 rule
set, including the ip6 lines, loads with `nft -f`. A controller-owned 0755 directory is not writable by the agent.
Deviation needed for section 4.1's `--tmpfs /run --remount-ro /run`: on this host /etc/resolv.conf is a symlink to
/run/systemd/resolve/stub-resolv.conf, so after the /run tmpfs bwrap fails with "Can't create file at
/etc/resolv.conf". Did instead: bind the sandbox resolver at the symlink's target inside the new /run tmpfs
(`--tmpfs /run --ro-bind <resolv.conf> <realpath of /etc/resolv.conf> --remount-ro /run`), falling back to
/etc/resolv.conf when it does not point into /run. The runtime uses the same order.

## 2026-10-02 worker_sandbox/contracts.py:57 (stage 2 step 3)
Expected: "`RuntimeSpec` gains ... defaults that keep every existing `host.json` valid" (stage 2, 5.1); tools/setup_host.py unchanged (5.6).
Observed: existing host.json files stay valid (the provisioned root-mode file reads back with mode 'root'), but
contracts.dumps(RuntimeSpec) now includes mode, subuid_base and subgid_base, so the stage 1 test that pinned
setup_host.host_document to the exact dumps bytes failed.
Did instead: tests/test_cli.py HostConfigTests now asserts that setup_host's bytes load to the same RuntimeSpec; setup_host.py unchanged.
inspect()'s informational runtime_digest changes value for root mode because the digest covers the new fields.

## 2026-10-02 worker_sandbox/rootless.py:46 worker root default (stage 2 step 4)
Expected: worker_root default `$XDG_STATE_HOME/worker-sandbox/worker`, owned by the sub-UID (stage 2, 4.3).
Observed: /home/<user> is 0750 and ~/.local, ~/.local/state are 0700, so host uid 100000 (inner 1000) cannot traverse to
anything under HOME; neither the bridge nor bwrap's bind source could reach that worker root. For the same reason the
4.4 read-only bind of an agent binary under /home cannot work on this host (root-owned copies are unaffected).
Did instead: default worker_root `/var/tmp/worker-sandbox-<user>/worker`; its parent is controller-owned 0755 and the worker
root itself is sub-UID-owned 0700. (First provisioned 0711; doctor attempt 1 showed the bridge needs read on the parent, see below.) The control root stays at
`$XDG_STATE_HOME/worker-sandbox/controller`. verify_model refuses a binary whose directories other users cannot traverse,
so a binary under a private HOME fails early with a clear message. Owner to confirm the location.

## 2026-10-02 worker_sandbox/rootless.py:135 bridge without --mount-proc
Expected: the bridge argv `unshare --user <map> --mount --mount-proc -- setpriv ...` (5.2).
Observed: `unshare: mount /proc failed: Operation not permitted` (a new /proc needs a pid namespace owned by the new user
namespace). Without --mount-proc the bridge runs as uid 1000, gid 1000, no groups.
Did instead: dropped only --mount-proc from the bridge (S4: remove the offending option, record it).

## 2026-10-02 worker_sandbox/rootless.py:233 setup jobs through a launcher
Expected: setup jobs run as a one-shot `unshare ... bwrap ...` through service_command with network=False; invoke is inherited (5.2).
Observed: inherited invoke launches with `checked_command(service_command(...), timeout=10)` and then observes; a one-shot
sandbox would block inside checked_command until the job ends and fail any job longer than 10 s (the doctor's provider
HTTPS probe alone can take longer).
Did instead: for network=False, service_command returns a tiny launcher argv that starts the one-shot chain in a new
session, records its pid and start time in `<control>/units/<unit>.json`, and exits at once, as systemd-run does.
state() and cleanup() read the same unit records, which also lets `recover` find a crashed controller's processes.

## 2026-10-02 worker_sandbox/rootless.py:205 sandbox mount order
Expected: Appendix A order, with `--tmpfs /run --remount-ro /run` (4.1) and `--ro-bind <resolv> /etc/resolv.conf`.
Observed: see the step 2 entry for /run; additionally `--remount-ro /var` before the worker-root bind would stop bwrap from
creating the mount point of a worker root under /var/tmp.
Did instead: `--remount-ro /var` follows the binds; the resolver is bound at the /etc/resolv.conf symlink target in the
/run tmpfs. Same options as Appendix A otherwise.

## 2026-10-02 worker_sandbox/rootless.py:158 verify_model in rootless mode
Expected: "root-owned requirement replaced by 'the binary and its directory are not writable by the sandbox'" (4.4).
Did instead: the binary must be readable and executable by other users and not world-writable, and every parent directory
must be traversable by other users and not world-writable unless sticky; the digest check is unchanged. Inside the sandbox
the whole host tree is read-only, so the binary cannot be changed there.

## 2026-10-02 worker_sandbox/rootless.py:62 provisioning the worker root
Expected: setup-rootless creates worker_root "through the bridge (inner 1000 creates it)" (5.4).
Observed: inner 1000 (host sub-UID) cannot create a directory in a controller-owned parent.
Did instead: provision() has inner root create the directory and chown it to inner 1000 in one `unshare --user <map>`
call; the host owner is the sub-UID base, mode 0700 (measured: 700 100000:100000, the controller cannot list it).

## 2026-10-02 worker_sandbox/__main__.py:39 choosing the host config (stage 2 step 5)
Expected: "after `hostconfig.read()`, choose `RootlessRuntime` when `spec.mode == 'rootless'`" (5.4), with hostconfig.py unchanged (S1)
and the doctor and live commands of sections 6 and 7.3 carrying no mode option.
Observed: hostconfig.read() reads only the root-mode control root, and this host has a root-mode host.json there, so those
commands would always run root mode.
Did instead: host_spec() uses this user's rootless host.json (`$XDG_STATE_HOME/worker-sandbox/controller/host.json`, written
only by setup-rootless) when it exists, else hostconfig.read(); `WORKER_SANDBOX_MODE=root` forces root mode. login, run,
recover and the doctor use it. The code default stays root (`RuntimeSpec.mode = "root"`); a user switches to rootless by
running setup-rootless. Owner to confirm, or choose an explicit option instead.
Provisioned on this host (no root): `worker-sandbox setup-rootless` created ~/.local/state/worker-sandbox/controller (0700,
controller) with host.json (mode rootless, sub-UID/GID 100000), /var/tmp/worker-sandbox-<user> (0711) and its worker root
(0700, 100000:100000).

## 2026-10-02 tools/doctor.py rootless doctor (stage 2 step 6)
Attempt 1 (verification/doctor-rootless-20261002T093434Z.json) failed in claim: the bridge's directory_fd opens every
ancestor with O_RDONLY|O_DIRECTORY, which needs read permission, and the worker root's parent was 0711. It left a lease
and the worker run directory; `worker-sandbox recover <run>` (rootless) released both. Fix: the parent is 0755
(provision() and this host). Attempt 2 (verification/doctor-rootless-20261002T093518Z.json) passed: mode rootless,
19 checks with the stage 1 names and order except agent-binary-read-only-in-sandbox (write open refused with EACCES,
`--version` printed 2.1.286), worker root empty through the bridge, no sub-UID process, no slirp4netns, no unit record.

## 2026-10-02 rootless credentials (stage 2 step 7)
Which happened (7.3): the owner copied the stage 1 staged logins with
`cp -a /var/lib/worker-sandbox-controller/credentials ~/.local/state/worker-sandbox/controller/credentials`; no new login.
Checked by name and mode only: codex/.codex/auth.json, claude/.claude/.credentials.json and claude/.claude/.claude.json, all 0600, directories 0700.

## 2026-10-02 rootless Codex acceptance: Codex's own sandbox cannot start (stage 2 step 7, STOP)
Run: verification/acceptance-rootless/runs/adbd5d0aaf3e4de991d0e8becd24c14c. exit 0, outcome completed, exit_code 0,
session 01a0fbf9-1506-7e41-aaf9-5436164b1ea7, but **no hello.txt**: every Codex tool call failed. Codex wraps shell
commands and apply_patch in its own bubblewrap sandbox, and each attempt printed "bwrap: No permissions to create a new
namespace"; Codex then reported "I couldn't create hello.txt" and exited 0. After the run: worker root empty (bridge),
no sub-UID process, no slirp4netns, no unshare, no unit record.
Measured cause: every process inside a bwrap sandbox on this host runs under the AppArmor label `bwrap//&unpriv_bwrap
(enforce)`, and a bwrap started from there cannot create namespaces (`nested rc=1`), with or without --unshare-user.
The same happens with a plain bwrap started from the controller's own shell, so it is the host policy
(kernel.apparmor_restrict_unprivileged_userns=1 with the shipped bwrap-userns-restrict profile), not the stage 2 layers.
`unshare -U` does succeed inside. Root mode is unaffected because its outer layer is systemd, not bwrap.
Did instead: nothing to the code; the Codex acceptance is not passed. Options for the owner:
(a) in rootless mode, run Codex with its own sandbox off (for example `-c sandbox_mode="danger-full-access"` or a
    permission profile without the Linux sandbox), so the worker-sandbox boundary is the only sandbox, as for Claude Code
    (permissions skipped); this needs a profiles.py change the brief does not enumerate;
(b) replace the nested bwrap with a mount sandbox built by inner root in the outer unshare (no unpriv_bwrap label), then
    measure whether Codex's bwrap works there; this is a redesign beyond section 4;
(c) accept that Codex is root-mode only on hosts with this AppArmor policy and document it.

## 2026-10-02 rootless Claude acceptance and the 7.4 MCP measurement (stage 2 step 7)
Claude acceptance passed: verification/acceptance-rootless/runs/234b34fdb8404d898649270638d7a3e9: exit 0, completed,
exit_code 0, session c236594e-e2a2-4cf9-8a21-484fc47b9b2d, hello.txt = "hello\n" (Write tool), model claude-opus-5-5,
permissionMode bypassPermissions (no refusal of --dangerously-skip-permissions at inner uid 1000); afterwards the worker
root was empty through the bridge, and no sub-UID process, slirp4netns, unshare, unit record or lease remained.
7.4 before: the system/init event lists `mcp_servers: []` and 22 tools, yet the final message says "the Exa, Slack and
Smartling connectors need to be authorized in your claude.ai connector settings", so the account connectors reach the
model's context without appearing in mcp_servers.
7.4 after (`-- --strict-mcp-config`, runs/35648372318b43a6b677f4c421f17323): exit 0, completed, hello.txt = "hello\n"
(Bash tool this time), init identical apart from run paths (`mcp_servers: []`, 22 tools), and no connector is mentioned
anywhere in either unit's stream. No other effect observed.
Did instead (5.4 / 7.4 enumerated edit): the Claude profile's command() adds `--strict-mcp-config` after
`--permission-prompts none`; tests/test_profiles.py updated. This applies to root mode too (shared profile).

# Owner decisions (stage 2, 2026-10-02, relayed by the worker-benchmark session)
- Worker root at /var/tmp/worker-sandbox-<user>/worker: approved, on condition that inspect keeps refusing a worker root
  not owned by the sub-UID base with mode 0700, that a worker root removed by systemd-tmpfiles is either recreated by
  setup-rootless or reported clearly by inspect, and that the README states it.
- Rootless host.json preferred when present, WORKER_SANDBOX_MODE=root forces root mode: approved; README states it;
  a --mode option is a later-stage item only.
- Setup jobs through a separate launcher: approved.
- --mount-proc removed from the bridge: approved; worker_files.py unchanged.
- Implemented (owner condition on the worker root): inspect refuses a missing worker root with a message naming
  systemd-tmpfiles and `worker-sandbox setup-rootless`; setup-rootless, when the rootless host.json exists and only its
  worker root is gone, recreates the worker root as recorded (provision_worker) and reports `repaired: true`; otherwise
  it still refuses an existing host.json. The owner and 0700 checks in inspect are unchanged.
