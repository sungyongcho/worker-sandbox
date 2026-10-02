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
