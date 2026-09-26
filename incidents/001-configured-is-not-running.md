# Configured Is Not Running

*Three custom enforcement hooks showed hundreds of gate-log records, passed their test suite, and provided no verified coverage during the session when failures were recorded. Here's what failed, what was repaired, what was verified, and what remains open.*

---

## Context

The Andon starter kit ships hooks invoked through `python3` — the execute bit is not required for those. This incident concerns three **custom local extensions** added on top of the starter kit and configured as direct commands in `settings.json`. That distinction is the failure mode: direct command invocation requires the execute bit; interpreter invocation does not.

---

## The setup

I added three enforcement hooks to my local configuration: a PreToolUse hook designed to block a class of consequential actions when the system was running autonomously, a PostToolUse hook, and a Stop hook. Only the PreToolUse hook runs before a tool executes; the others run after or at session end.

The hooks appeared configured. The fire log showed hundreds of historical gate-log records that appeared to indicate activity, but did not establish successful direct-command invocation. The test suite was green.

---

## The failure

During a session on September 10, 72 unique invocation failures were recorded across three hooks. The records were present in the transcript but not acted on. The execute bit was missing from all three scripts.

The harness invokes these custom hooks as direct commands — the path set in `settings.json`. Direct command invocation requires the execute bit. Without it, every invocation returned `Permission denied` and failed without blocking the operation.

The test suite invokes the pre-tool gate script through a Python interpreter subprocess. Interpreter invocation does not require the execute bit. The tests were calling the script through a path that couldn't reproduce the production failure.

*Count note: 72 unique structured failure records were parsed from the incident transcript by UUID: 53 PreToolUse, 14 PostToolUse, and 5 Stop hook failures. System summary messages in the same transcript repeat some of those events and were not counted separately. A secondary narrative in the session record uses a different breakdown; the UUID-parsed count is the primary source.*

---

## What the visible checks showed

- `ls -l`: showed `-rw-r--r--` on all three scripts — the missing execute bit was detectable here
- Running the scripts directly: failed with `Permission denied` — same failure mode as the harness
- Test suite (pre-tool gate): green, because the inspected test helper uses interpreter invocation, not direct invocation

The missing execute permission is not hidden — `ls -l` exposes it. The gap was that no routine confirmed these scripts could be invoked via the direct command path the harness uses.

---

## How it was found

Not through a monitor. Not through the gate detecting its own failure. Through a manual audit of the session transcript looking for patterns in what was failing — and finding 72 failures that had not been acted on.

The failures were recorded on September 10; `chmod +x` was applied at 14:47 UTC the same day, after the failures were observed. Later forensic review examined the fire log for historical context; how long the execute bit had been missing before September 10 is not established. Duration unknown.

One important boundary: the PreToolUse hook only activates when an autonomous-mode environment variable is set by the overnight launcher. Whether autonomous mode was running during the period when failures were recorded is not established by the transcript. Unverified exposure is not the same as demonstrated harm.

---

## The repair and what was verified

`chmod +x` was applied to all three scripts. All three are currently mode 0755.

After the repair, manual checks were run. The pre-tool gate received a launch test, a deny test, and an attended-allow pair. The other two hooks received launch checks. These establish the behavior of the checked scripts at the time of the check, under the specific inputs tested.

What was not established: an automated test that would detect this class of failure if it recurred.

---

## Status

*Closed 2026-09-24: pre-tool direct-launch test gap.*

The automation health monitor design rules prescribe a liveness check: invoke each configured hook as a direct command with a benign payload and alarm on a launch/permission failure; separately verify deny behavior; cross-check configuration against disk in both directions.

Direct-command regression coverage is implemented for the pre-tool gate. Automated coverage of the other two hooks, configuration reconciliation, and harness enforcement remain outside this closure's scope. See Evidence for the test source. The inspected test helper for the pre-tool gate used interpreter invocation and would not have caught the incident; the new test file uses direct command invocation.

A design rule in a document is the same category of thing as a gate with no execute bit: it expresses the intent without enforcing it.

---

## The defect class

This is a member of a recurring family: **system leaned on assumption, not enforcement.**

The assumption was: if the file exists, the tests pass, and the fire log shows activity, the gate is running. None of those checks exercise the direct command invocation path.

The assumption collapsed four distinct states into one. A file can exist and be referenced in configuration. A configured command can still fail to launch. A launchable hook may not have been exercised under the relevant runtime condition. An exercised hook may still fail to enforce the intended outcome.

**Configured ≠ Launchable ≠ Exercised ≠ Enforcing.**

The fix has two layers. The first layer — `chmod +x` and manual post-repair checks — is done. The second layer — an automated liveness test that exercises the direct command invocation path — is also done.

A defect isn't closed when the symptom is repaired. It's closed when recurrence becomes detectable.

---

## What a complete fix looks like

**Step 0 — broken-launch fixture:** Before testing logic, verify the checker can detect a launch failure. Remove execute permission from an isolated copy of the hook; require the checker to identify the failure as a launch/permission failure, distinct from exit 2 (policy denial). If this fixture doesn't fail when it should, the test cannot catch the incident it exists to prevent.

**Step 1 — benign-payload launch test:** Invoke the hook as a direct command with a payload it should process without error. A launch or permission failure — distinct from a policy denial (exit 2) — means the hook cannot be invoked via the production path. Exit 0 means the hook is reachable.

**Step 2 — deny test:** Send a payload the pre-tool gate should block. Verify exit 2 and the expected deny reason in stderr.

**Step 3 — allow control:** Send a payload the pre-tool gate should pass. Verify exit 0 and that the hook's output carries no deny signals (`continue`, `permissionDecision`, `decision`). This tests that the hook signals correctly — not that the harness actually allowed the tool call. Whether the harness enforces the signal is outside the scope of a hook test; the harness reads the hook's output and decides. A hook that signals correctly is the prerequisite; harness behavior requires a full integration test.

One design note: the highest-confidence liveness check reads the actual configured command from `settings.json` and exercises that command using the same invocation semantics as the harness — rather than constructing an independent assumption about how hooks are invoked. Otherwise the checker can recreate the same divergence: production config says A, health checker independently assumes B.

Isolate each fixture from production inputs. Verify side effects independently.

The liveness test at `.claude/scripts/tests/test_hook_liveness.py` implements these steps against the pre-tool gate. It uses `[hook_path]` (direct command invocation), not `[sys.executable, hook_path]` (interpreter invocation). The PostToolUse and Stop hooks are not covered here — they have no deny logic and require different test shapes. All four tests pass; three mutations confirmed they go RED on the conditions they exist to catch.

---

## Evidence

- Incident transcript: `.claude/projects/[session].jsonl`, permission-error attachments, 72 unique records by UUID, September 10 2026, 13:47–14:47 UTC; repair at 14:47:32 UTC
- Fire log: `.claude/logs/enforcement_hook_fires.jsonl`, 317 pre-repair gate-log entries (fields: timestamp, tool, target, decision, reason — not test outcomes or invocation method)
- Repair: September 10 2026, mode change to 0755; manual launch, deny, and attended-allow checks on the pre-tool gate; launch checks on the other two hooks
- Prescribed check: `automation-health-monitor` SKILL.md, monitor design rule 9
- Inspected test source: `.claude/scripts/tests/test_enforcement_hook.py`, interpreter invocation confirmed for this file
- Andon starter kit invocation reference: `HOOK_INSTALL.md`, `python3` explicit throughout
- Liveness test: `.claude/scripts/tests/test_hook_liveness.py`, direct command invocation, Steps 0–3 against the pre-tool gate; 4/4 tests pass, 3/3 mutations RED (2026-09-24); Step 3 verifies hook output signals, not harness enforcement
