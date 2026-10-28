# Defect Ledger

Real defects, real countermeasures — the record this whole system grew out of.

Each entry is **defect → root cause → countermeasure → result.** These are de-identified: the specifics (project names, data, file paths) are stripped; the defect *class* and the fix are what transfer. The point of the ledger is the point of the whole project — **a fix needs a tested countermeasure and a clear account of what can still fail.** New entries get added as new defects get caught. You'll recognize your own week in here.

> Want to contribute one? Open a PR with the same four-line shape. The most useful thing you can add is a defect *class* others will hit too.

---

## How each entry is tracked: verdict and control

A countermeasure is a control. Controls age. The risk behind one can go away, a better control can replace it, or it can turn out to be wrong. So each entry ends with two more lines:

```text
**Verdict:** confirmed — <where the evidence is>
**Control:** id: D-018 · status: test · review_by: 2027-04-27 · modified: 2026-10-27 · related: derived:D-014
```

**Verdict** is about the defect. Is it real?

- `confirmed` — the evidence is complete. The reason says where it is.
- `needs_validation` — something is still unproven. The reason names the exact unresolved fact.
- `rejected` — the defect was disproved. The entry stays on record, and its control must be retired.

**Control** is about the countermeasure. How far is it trusted?

- `experimental` — written down. No automated check in this repo pins it yet.
- `test` — an automated check here pins it. It runs in warn mode or is still being tuned.
- `stable` — pinned by a check, used on real work, and promoted by the maintainer (for a hook, to block mode).
- `deprecated` — retired. The risk is gone, or another control replaced it.
- `withdrawn` — retracted. The control was wrong. It stays on record so nobody rebuilds it.

The path is the same as for the hooks: **warn first, block once trusted.** A control moves `experimental` → `test` → `stable` as evidence builds up. It never jumps straight to block.

**review_by** is a date. Every active control (`experimental`, `test`, `stable`) has one. It falls on or after the `modified` date and no more than 365 days after it. On that date someone re-checks the control. They then set a new date, promote it, or retire it. If the date passes with no action, `scripts/check_ledger.py` fails, and so does the test suite. A forgotten control breaks the build instead of rotting quietly.

**Retire, never delete.** A retired control keeps its entry with status `deprecated` or `withdrawn`. The record of why it existed is the point of the ledger.

The other fields: `id` is a stable `D-NNN` number. `modified` is the date the Control line last changed. `related` links entries as `<type>:<id>`, where type is `derived` (built on that entry's control), `obsolete` (this control replaces that one), `merged` or `renamed`. Lines inside a code fence are examples: the checker does not read them as an entry's Verdict or Control line.

Run `python3 scripts/check_ledger.py` to check every entry.

---

## The patterns underneath

Read enough of these and the specific bug stops mattering — they cluster into a handful of recurring failure modes that aren't really about AI at all. They're failure modes of *any* intelligence working under consequence. Spot the family and you can catch the next one before it happens:

- **Claim exceeded evidence** (false "done" · fixed-5-claimed-14 · tested-the-proxy · relayed-a-finding) → *a claim is only as good as the verification behind it.*
- **Decision outran reality** (it-doesn't-exist · rebuilt-what-existed · acted-on-yesterday's-map) → *check the current state before you act, not your memory of it.*
- **System leaned on assumption, not enforcement** (four-parsers · the-gate-that-jammed · passed-by-luck) → *if it isn't mechanically enforced, it drifts.*
- **Pressure replaced evidence** (reversed-a-right-answer-when-pushed) → *"are you sure?" is a request for evidence, not a reason to fold.*

The four-line entries below are the raw material; these are the heuristics they distill into.

---

### 2026-04 — The false "done"

**Defect:** The agent said a change was "complete and tested." It had written the code but never run the tests. I trusted it, moved on, and the gap surfaced hours later.
**Root cause:** "Done" is the single most expensive word an agent says, and nothing checked it. Polite reminders ("remember to verify") don't survive the moment.
**Countermeasure:** A Stop hook (`claim_check_hook.py`) that reads the turn-ending message for done-claims and requires a verification log entry before the turn can end. Warn first, block once trusted.
**Result:** When the detector recognizes a done-claim, the hook makes an unlogged verification gap visible instead of letting it pass silently. It catches forgetting-to-verify, not lying-about-it: the agent still writes its own evidence, and a done-claim phrased outside the patterns still passes unseen (see the recall entry below).

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-001 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

### 2026-04 — "That doesn't exist" (it did)

**Defect:** Told to find a thing, the agent grepped one keyword, got nothing, and reported it absent. It was there the whole time — under a different name.
**Root cause:** Absence-from-a-token-search was treated as absence-of-the-thing. Files get named generically; projects get renamed.
**Countermeasure:** Before concluding "X isn't here," search synonyms and old names, grep the *content* (not just the title), and check the version-control log for when it was added.
**Result:** "It doesn't exist" became a claim that has to be earned, not a first impression.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-002 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — Fixed five, claimed fourteen

**Defect:** Fixed the instances of a problem in the files I was looking at, then reported the *whole class* fixed. Nine more were sitting in files I hadn't opened.
**Root cause:** Scoping the claim to what I'd touched, not to what existed.
**Countermeasure:** Before any "all / complete / whole" claim about a class, `grep` the full set first and verify each — then scope the claim to exactly what was verified ("5 of the daily surfaces," not "the whole stream").
**Result:** Completeness claims got narrower and truer.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-003 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — Four parsers, four slightly different schemas

**Defect:** A config file was read by several scripts, each written from memory. One read the fields in a different order. It didn't crash — it silently returned wrong results.
**Root cause:** The schema was a *convention*, not a thing that's enforced. Every from-memory parser is a chance to drift.
**Countermeasure:** One canonical loader that every consumer imports. If the structure isn't checked in one place, it isn't real.
**Result:** Schema-drift — the most expensive silent-bug class — stopped being possible for that file.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-004 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — Tested the script, not the thing that runs

**Defect:** Declared a hook "verified end-to-end" after running the inner script by hand. The harness actually invokes a *wrapper* that reads stdin — which was never exercised.
**Root cause:** "Works" was checked against a proxy, not the real entry point.
**Countermeasure:** When the claim is "it works end-to-end," the test must hit the actual production path — the wrapper the harness calls, under the real environment.
**Result:** "Verified" started meaning the real path, not a convenient stand-in.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-005 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — `$100` matched `$1000`

**Defect:** A checker used substring matching to compare a price. `"$100"` matched inside `"$1000"`, so a 10× error sailed through as "no change."
**Root cause:** Substring containment where word-boundary matching was needed.
**Countermeasure:** Word-boundary regex with the negative lookahead; never naive `in` for values that have longer supersets.
**Result:** The checker started catching the drift it existed to catch.

**Verdict:** confirmed — reproducible in one line: `"$100" in "$1000"` is `True` in Python
**Control:** id: D-006 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — Repeated a subagent's "finding" as fact

**Defect:** A helper agent reported a surprising result; I relayed it as true. It was wrong — the agent had missed a detail (a symlink, in that case).
**Root cause:** Treating a subagent's output as a conclusion instead of an input.
**Countermeasure:** Spot-check every *surprising* subagent finding by re-running its evidence yourself before repeating it.
**Result:** Surprises now get a second look; fewer confident-but-wrong relays.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-007 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — Acted on yesterday's map

**Defect:** Started a session from the last handoff note and recommended work that was already done — a parallel session had shipped it between the handoff and now.
**Root cause:** A handoff is a snapshot, not live state. Work happens between writing it and reading it.
**Countermeasure:** Before acting on a handoff, re-read the version-control log since its timestamp. Freshest reality wins.
**Result:** Fewer "we already did that" loops.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-008 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-05 — The gate that jammed the worker

**Defect:** Shipped an enforcement hook in block-mode-by-default. First real use, it blocked a legitimate turn with no obvious way out. Trust in the hook cratered immediately.
**Root cause:** A safety gate with no escape hatch and no warm-up period trains the operator to fight it.
**Countermeasure:** Default to *warn*; provide an env-var escape; promote to *block* only after it's been tuned. Make every gate advisory until proven.
**Result:** Enforcement that people keep on, instead of ripping out.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-009 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

### 2026-06 — The test that passed by luck

**Defect:** A test was green, so the fix looked solid. The test was asserting something that happened to be true in one case, not the property that actually mattered.
**Root cause:** A green test on an unsound assumption is worse than no test — it locks the false belief in.
**Countermeasure:** Assert the real invariant, then mutate the code to confirm the test *fails* on a plausible bad version. If a bad mutation survives, the test isn't guarding anything.
**Result:** Tests that catch regressions instead of decorating them.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-010 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

### 2026-06 — Rebuilt what already existed

**Defect:** Asked for a capability, the agent built it from scratch. The codebase already had it. Now there were two, subtly different.
**Root cause:** The search felt slower than the build, so it got skipped.
**Countermeasure:** Search-before-building, every time: grep the code, check memory, look for an existing tool. A 30-second search beats three sessions of duplicate-bug cleanup.
**Result:** Less duplication, fewer subtly-divergent twins.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-011 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-06 — Reversed a right answer when pushed

**Defect:** Challenged with "you sure?", the agent retracted a *correct* claim after eyeballing partial evidence — overcorrecting into a new error.
**Root cause:** Reading "you sure?" as "you were wrong" instead of "check it with more sources."
**Countermeasure:** Treat a challenge as a trigger to gather more evidence and compute the *full* picture — then keep or change the claim on the evidence, not the pressure.
**Result:** Challenges make answers more right, not more flip-floppy.

**Verdict:** needs_validation — the source incident is first-hand and de-identified; no record of it is in this repo
**Control:** id: D-012 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-09 — The gate that wasn't running

**Defect:** An enforcement hook appeared fully configured: on disk, wired in the harness config, hundreds of historical gate-log records in the fire log that appeared to indicate activity, but did not establish successful direct-command invocation. During an observed session, dozens of invocation failures were recorded across three hooks and were present in the transcript but not acted on. The execute bit was missing. Tests invoked the scripts through an interpreter, which doesn't require the execute bit; the harness invoked them as direct commands, which does. Two invocation paths, silently diverged.
**Root cause:** "Tested" and "tested via the production invocation path" are different claims. Every test used an interpreter call that bypassed the requirement that would have caught the failure. The missing execute bit is detectable with `ls -l` — the deeper gap was that no routine confirmed the scripts could be invoked via the direct command path the harness uses.
**Countermeasure:** Before declaring any hook live, invoke it as a direct command with a benign payload and alarm on a launch/permission failure (distinct from a policy denial). Cross-check configuration against disk in both directions. These checks belong in an automated test — not just a design rule.
**Result:** A design rule now prescribes the check, and an automated liveness test implementing Steps 0–3 (direct command invocation, broken-launch fixture, deny test, allow control) was subsequently built and runs against the pre-tool gate. See `incidents/001-configured-is-not-running.md`.

**Verdict:** confirmed — `incidents/001-configured-is-not-running.md` (72 failure records parsed from the session transcript)
**Control:** id: D-013 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-09 — The gate that could be alibi'd

**Defect:** The claim-check gate accepted *any* fresh verification entry as satisfying *any* done-claim. It checked only that some recent log line existed — never that the entry referred to the claim, or came from the same session. So a bare `log_claim "done" "checked"` cleared the gate; a genuine verification of task A licensed an unverified claim about task B; and — worst — on a shared home directory one session's entry cleared every other session's claim, invisibly.
**Root cause:** Freshness was standing in for verification. "A recent entry exists" is not "this claim was verified" — the gate measured the wrong thing, and Goodhart did the rest: the block message even handed back the command that clears it, training "run log_claim" over "run the check."
**Countermeasure:** Bind the entry to the claim *and* the session. `log_claim.py` stamps the session id (from `CLAUDE_CODE_SESSION_ID`) and a content-token fingerprint of the claim *and* its verification; for claims with subject tokens, the gate checks for a fresh same-session entry whose fingerprint overlaps that claim's sentence. Subjectless claims use a same-session freshness fallback (see the README limits). Closure verbs and selected generic dev nouns are stopworded so "done" cannot match on that word alone. The block message names the verification, not the unblock command. Legacy (pre-binding) entries are honored only while the log has no binding-schema entry. Once a binding-schema entry exists, older entries no longer qualify; in-flight work may need a new-format receipt.
**Result:** New-format entries for claims with subject tokens must match the session and share tokens. Subjectless and legacy-only cases use the fallbacks described above. It is a forcing function, not a cryptographic guarantee — token overlap establishes topical correspondence, not proof (an agent that names the subject in its evidence without doing the work still passes, on the record). Reported by an external contributor with reproductions (#2); the three alibis are RED-before / GREEN-after tests.

**Verdict:** confirmed — reproductions in issue #2; the three alibi tests in `hooks/tests/test_claim_check_hook.py`
**Control:** id: D-014 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

### 2026-09 — Tightened for precision, lost the recall — then overshot fixing it

**Defect:** The claim detector caught 4 of 13 ordinary done-claims. `Implemented the migration.` fired; `I've implemented the migration.` — how agents actually write — did not. `Task complete!` and `The fix is in place.` passed unseen. Reported with a repro in issue #3.
**Root cause:** Every false-positive fix had narrowed the patterns and nothing measured what the narrowing cost. Both sentence-start patterns were anchored so any leading subject defeated them.
**Countermeasure:** Additions that stand on their own — a first-person lead-in (the determiner rule protecting `Fixed income securities…` untouched), the missing standalone verbs, a subject-noun closure form, and state verbs each carrying their own guard. Every accumulated example is a checked-in corpus, and the test that matters asserts the patch is *additive*: no previously detected example in that corpus may stop being caught.
**Result:** 11 of 13 on the reported set; 0 new false fires and 0 lost detections in the accumulated corpus. The two still missed are result-claims (`the tests pass`, `CI is green`), left deliberately — see the entry below, which is why.

**Verdict:** confirmed — repro in issue #3; `hooks/tests/test_claim_corpus.py`
**Control:** id: D-015 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

### 2026-09 — The fix that kept breaking what it was fixing

**Defect:** Catching result-claims (`the tests pass`) needs the detector to know when a clause *asserts* something, so exemptions were added for negation, attribution and conditionals. Each round of review found claims those exemptions had silently suppressed: four, then two, then one. Each fix was correct and produced the next one.
**Root cause:** Recall was being bought with exemptions, and an exemption is a blanket — it cannot see which claims it covers. Every new one widened the blast radius, so the defect rate per fix stayed roughly constant instead of falling. The reviews were catching instances; nobody was counting the class.
**Countermeasure:** Drop the feature that needed the exemptions rather than keep tuning them, and replace instance-checking with a property: a corpus-wide test that the patch only ever adds detections. Mutation-checked — reintroduce a broad exemption and it fails.
**Result:** Result-claims stay undetected, which is a real loss stated plainly rather than a target met. The broad exemptions were removed, and the corpus tests check that its recorded detections remain intact. Inputs outside that corpus can still expose regressions. *An exemption that can suppress a claim you cannot enumerate is a liability priced as a feature.*

**Verdict:** needs_validation — the suppressed-claim counts per review round (four, two, one) sit in PR #4's review history and were not re-counted for this entry
**Control:** id: D-016 · status: test · review_by: 2027-04-27 · modified: 2026-10-27 · related: derived:D-015

### 2026-09 — The rollback that could roll back to itself

**Defect:** A deploy-rehearsal gate proved rollback by rebuilding the previous commit's image, redeploying it by digest against the same database, and checking that it came up with the existing data intact. Caught in design, before the gate's first run: if the previous commit only changed docs, it builds a byte-identical image with the same digest, and the gate "rolls back" by redeploying the exact thing it claims to have rolled back from. Green, and proving nothing.
**Root cause:** The gate compared two things that could silently become the same thing. Nothing required its two sides to differ, so the pass condition was reachable without the property ever being exercised.
**Countermeasure:** Stamp every build with the commit it came from (an image revision label), so two commits always produce two digests. The gate asserts *digest A ≠ digest B*, and that each label names its own commit, before it redeploys anything. The label reads like metadata, so it's recorded as load-bearing next to the code: deleting it makes nothing fail loudly; it makes the proof go quietly vacuous.
**Result:** When its two sides collapse into one, the gate now fails instead of passing. Residual: with a docs-only parent the two images differ only by the label, so the gate proves the redeploy-by-digest mechanism and that data survives the swap, not that older *behavior* came back.

*Contributed by @w-30x (PR #6).*

**Verdict:** needs_validation — found in design before the gate's first run; no build here shows a docs-only parent producing the same image digest
**Control:** id: D-017 · status: experimental · review_by: 2027-01-27 · modified: 2026-10-27

### 2026-10 — The witness who wrote their own alibi

**Defect:** After binding (above), a verification entry had to be fresh, same-session and on-topic — but the agent still wrote it. `log_claim.py "migration complete" "ran the migration tests"` cleared the gate whether or not a test had run. The gated actor and the evidence author were the same actor.
**Root cause:** No content check can separate an honest entry from a self-alibi when the agent writes both the claim and the evidence: quoting yourself is exactly what an honest agent does too. The lever is the *channel*, not the content. (This argument is @w-30x's, on #2.)
**Countermeasure:** An opt-in evidence mode (`CLAIM_CHECK_EVIDENCE=tool`) that ignores the agent's line and requires a passing verification run the harness recorded in the session transcript: allowlisted command, same `sessionId`, inside the freshness window, a finished non-error result, and a command line that matches a small fixed grammar so its exit status *is* the verification's. The recorded-result markers were read off real transcripts first, which turned up three ways a run reports success without having succeeded: a backgrounded command (`backgroundTaskId`, including auto-backgrounding), a command that hit the tool timeout (`timedOutAfterMs`), and a nonzero exit reinterpreted as success (`returnCodeInterpretation`) — all with `is_error: false`. A first version parsed arbitrary shell and checked separators; four rounds of review kept finding lines where the parser and the shell disagreed (a `#` comment swallowing a newline, a mid-word quote, `pipefail` "set" inside a dead branch). The fix was to stop parsing shell and accept only an allowlisted grammar.
**Result:** A log line with no run behind it no longer clears a done-claim in that mode. The tests drive the real hook through its subprocess entry point; because a "must block" test also passes against a hook that blocks everything, each guard is pinned by a mutation script that breaks it and checks a test fails. It raises the cost of a false "done" from writing one line to forging harness records — deliberate, done through a tool call the transcript records, though the forging code can hide inside a script that call runs — not to impossible. A passing run still says nothing about whether the test was meaningful, about the claim's subject, or about the program behind the command name. Design from three outside sources: @w-30x on #2 (root-cause argument, this-session window, allowlist, forgery residual), the Assay-generated review @jojig-dao shared on #1 (§2, a harness-written tool-use log; its recorder and run citation are not built), and the transcript-reading idea in @alex-spataru's Serial-Studio adaptation of this hook.

**Verdict:** confirmed — `test_self_alibi_log_line_without_run_blocks` in `hooks/tests/test_tool_evidence.py` shows a bare log line clears the gate in the default log mode
**Control:** id: D-018 · status: test · review_by: 2027-04-27 · modified: 2026-10-27 · related: derived:D-014

### 2026-10-16 — Cross-model review of the evidence mode (GPT-6 Astra, read-only)

**Defect 1:** `make test --old-file=test` (also `--assume-old`, `-o`, `-W`, `--what-if`, `--eval`) tells GNU make the target is already up to date or injects a recipe. A failing `.PHONY: test` recipe exits 0 without running, and tool mode credited it.
**Countermeasure:** the six long flags and letters `o W E` are banned for `make`; a test runs real `make` on a failing recipe with `--old-file=test` to prove the bypass is real, and 11 must-block forms pin the parser.

**Defect 1b (second critic, same night):** the make assignment check matched only `NAME=`; `SHELL:=/usr/bin/true`, `::=`, `+=`, `?=`, `.SHELLFLAGS=-nc` passed it. `MAKEFILES=inj.mk` in the environment injected a makefile. `-e` / `--environment-overrides` let an allowed `VAR=value` replace a recipe variable. All three reproduced on GNU Make 3.81.
**Countermeasure:** any non-flag make word containing `=` is refused; `MAKEFILES` joins the denied environment names; `-e` and `--environment-overrides` are banned. Nine more must-block forms, a real-make reproduction for `SHELL:=` and `MAKEFILES`, three mutants.

**Defect 2:** malformed hook stdin, a missing `transcript_path`, or a transcript that does not exist or cannot be read each returned silent success in `tool`/`both` mode, bypassing the fail-closed handler added for internal errors.
**Countermeasure:** `_unavailable()` fails closed in `tool`/`both` (warn in warn mode), passes in `log` mode and `mode=off`; `stop_hook_active` still passes first. Unparseable stdin warns rather than blocks (the critic's loop case: `stop_hook_active` cannot be read, so a block could re-fire every stop). Five tests and five mutants cover it.

**Defect 3:** an unquoted glob in the command line (`pytest --co*`, `pytest *`) is expanded by the shell before the verifier runs; with a file named `--collect-only` in the directory it becomes a no-run flag the parser never saw. Reproduced under `bash`.
**Countermeasure:** the lexer refuses unquoted `* ? [`; quoted globs (`'tests/test_*.py'`) stay creditable. Five must-block and two must-pass forms, one mutant.

**Defect 4 (test quality):** the numeric-timestamp fixture was rejected at id pairing before the timestamp check, so it never exercised that guard; and the mutation script counted any nonzero pytest exit, including a collection error, as a kill.
**Countermeasure:** two corrupt-one-field tests (numeric / missing timestamp) with the restored record as the positive control; a kill now requires a named failing test, and an infrastructure error is reported as ERROR.

**Round 2 (same reviewer, 2026-10-17 00:13):** four P2s. A transcript that vanishes or becomes unreadable AFTER the existence check still read as "no message" (silent pass): the readers now raise `TranscriptUnavailable` in tool/both mode and main fails closed. A root-skipped mutation returned exit 0: a skip is now a survivor unless `MUTATION_ALLOW_SKIPS=1`. HOOK_INSTALL §6 still permitted unquoted globs: corrected. The real-make premise tests inherited `MAKEFLAGS`: make-related variables are stripped.

**Still open (design call, not patched):** a done-claim older than 30 s is treated as a stale prior turn and never blocks (inherited; `read_last_assistant_message`).

**Verdict:** confirmed — real-make reproductions and must-block forms in `hooks/tests/test_tool_evidence.py`
**Control:** id: D-019 · status: test · review_by: 2027-04-27 · modified: 2026-10-27 · related: derived:D-018
