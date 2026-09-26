# Defect Ledger

Real defects, real countermeasures — the record this whole system grew out of.

Each entry is **defect → root cause → countermeasure → result.** These are de-identified: the specifics (project names, data, file paths) are stripped; the defect *class* and the fix are what transfer. The point of the ledger is the point of the whole project — **a defect isn't closed when you fix it, it's closed when you've made it un-repeatable.** New entries get added as new defects get caught. You'll recognize your own week in here.

> Want to contribute one? Open a PR with the same four-line shape. The most useful thing you can add is a defect *class* others will hit too.

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

### 2026-04 — "That doesn't exist" (it did)

**Defect:** Told to find a thing, the agent grepped one keyword, got nothing, and reported it absent. It was there the whole time — under a different name.
**Root cause:** Absence-from-a-token-search was treated as absence-of-the-thing. Files get named generically; projects get renamed.
**Countermeasure:** Before concluding "X isn't here," search synonyms and old names, grep the *content* (not just the title), and check the version-control log for when it was added.
**Result:** "It doesn't exist" became a claim that has to be earned, not a first impression.

### 2026-05 — Fixed five, claimed fourteen

**Defect:** Fixed the instances of a problem in the files I was looking at, then reported the *whole class* fixed. Nine more were sitting in files I hadn't opened.
**Root cause:** Scoping the claim to what I'd touched, not to what existed.
**Countermeasure:** Before any "all / complete / whole" claim about a class, `grep` the full set first and verify each — then scope the claim to exactly what was verified ("5 of the daily surfaces," not "the whole stream").
**Result:** Completeness claims got narrower and truer.

### 2026-05 — Four parsers, four slightly different schemas

**Defect:** A config file was read by several scripts, each written from memory. One read the fields in a different order. It didn't crash — it silently returned wrong results.
**Root cause:** The schema was a *convention*, not a thing that's enforced. Every from-memory parser is a chance to drift.
**Countermeasure:** One canonical loader that every consumer imports. If the structure isn't checked in one place, it isn't real.
**Result:** Schema-drift — the most expensive silent-bug class — stopped being possible for that file.

### 2026-05 — Tested the script, not the thing that runs

**Defect:** Declared a hook "verified end-to-end" after running the inner script by hand. The harness actually invokes a *wrapper* that reads stdin — which was never exercised.
**Root cause:** "Works" was checked against a proxy, not the real entry point.
**Countermeasure:** When the claim is "it works end-to-end," the test must hit the actual production path — the wrapper the harness calls, under the real environment.
**Result:** "Verified" started meaning the real path, not a convenient stand-in.

### 2026-05 — `$100` matched `$1000`

**Defect:** A checker used substring matching to compare a price. `"$100"` matched inside `"$1000"`, so a 10× error sailed through as "no change."
**Root cause:** Substring containment where word-boundary matching was needed.
**Countermeasure:** Word-boundary regex with the negative lookahead; never naive `in` for values that have longer supersets.
**Result:** The checker started catching the drift it existed to catch.

### 2026-05 — Repeated a subagent's "finding" as fact

**Defect:** A helper agent reported a surprising result; I relayed it as true. It was wrong — the agent had missed a detail (a symlink, in that case).
**Root cause:** Treating a subagent's output as a conclusion instead of an input.
**Countermeasure:** Spot-check every *surprising* subagent finding by re-running its evidence yourself before repeating it.
**Result:** Surprises now get a second look; fewer confident-but-wrong relays.

### 2026-05 — Acted on yesterday's map

**Defect:** Started a session from the last handoff note and recommended work that was already done — a parallel session had shipped it between the handoff and now.
**Root cause:** A handoff is a snapshot, not live state. Work happens between writing it and reading it.
**Countermeasure:** Before acting on a handoff, re-read the version-control log since its timestamp. Freshest reality wins.
**Result:** Fewer "we already did that" loops.

### 2026-05 — The gate that jammed the worker

**Defect:** Shipped an enforcement hook in block-mode-by-default. First real use, it blocked a legitimate turn with no obvious way out. Trust in the hook cratered immediately.
**Root cause:** A safety gate with no escape hatch and no warm-up period trains the operator to fight it.
**Countermeasure:** Default to *warn*; provide an env-var escape; promote to *block* only after it's been tuned. Make every gate advisory until proven.
**Result:** Enforcement that people keep on, instead of ripping out.

### 2026-06 — The test that passed by luck

**Defect:** A test was green, so the fix looked solid. The test was asserting something that happened to be true in one case, not the property that actually mattered.
**Root cause:** A green test on an unsound assumption is worse than no test — it locks the false belief in.
**Countermeasure:** Assert the real invariant, then mutate the code to confirm the test *fails* on a plausible bad version. If a bad mutation survives, the test isn't guarding anything.
**Result:** Tests that catch regressions instead of decorating them.

### 2026-06 — Rebuilt what already existed

**Defect:** Asked for a capability, the agent built it from scratch. The codebase already had it. Now there were two, subtly different.
**Root cause:** The search felt slower than the build, so it got skipped.
**Countermeasure:** Search-before-building, every time: grep the code, check memory, look for an existing tool. A 30-second search beats three sessions of duplicate-bug cleanup.
**Result:** Less duplication, fewer subtly-divergent twins.

### 2026-09 — Tightened for precision, lost the recall — then overshot fixing it

**Defect:** The claim detector caught 4 of 13 ordinary done-claims. `Implemented the migration.` fired; `I've implemented the migration.` — how agents actually write — did not. `Task complete!` and `The fix is in place.` passed unseen. Reported with a repro in issue #3.
**Root cause:** Every false-positive fix had narrowed the patterns and nothing measured what the narrowing cost. Both sentence-start patterns were anchored so any leading subject defeated them.
**Countermeasure:** Additions that stand on their own — a first-person lead-in (the determiner rule protecting `Fixed income securities…` untouched), the missing standalone verbs, a subject-noun closure form, and state verbs each carrying their own guard. Every accumulated example is a checked-in corpus, and the test that matters asserts the patch is *additive*: nothing the previous detector caught may stop being caught.
**Result:** 11 of 13 on the reported set, 0 new false fires, 0 regressions. The two still missed are result-claims (`the tests pass`, `CI is green`), left deliberately — see the entry below, which is why.

### 2026-09 — The fix that kept breaking what it was fixing

**Defect:** Catching result-claims (`the tests pass`) needs the detector to know when a clause *asserts* something, so exemptions were added for negation, attribution and conditionals. Each round of review found claims those exemptions had silently suppressed: four, then two, then one. Each fix was correct and produced the next one.
**Root cause:** Recall was being bought with exemptions, and an exemption is a blanket — it cannot see which claims it covers. Every new one widened the blast radius, so the defect rate per fix stayed roughly constant instead of falling. The reviews were catching instances; nobody was counting the class.
**Countermeasure:** Drop the feature that needed the exemptions rather than keep tuning them, and replace instance-checking with a property: a corpus-wide test that the patch only ever adds detections. Mutation-checked — reintroduce a broad exemption and it fails.
**Result:** Result-claims stay undetected, which is a real loss stated plainly rather than a target met. In exchange the regression class is gone by construction, not by vigilance. *An exemption that can suppress a claim you cannot enumerate is a liability priced as a feature.*

### 2026-06 — Reversed a right answer when pushed

**Defect:** Challenged with "you sure?", the agent retracted a *correct* claim after eyeballing partial evidence — overcorrecting into a new error.
**Root cause:** Reading "you sure?" as "you were wrong" instead of "check it with more sources."
**Countermeasure:** Treat a challenge as a trigger to gather more evidence and compute the *full* picture — then keep or change the claim on the evidence, not the pressure.
**Result:** Challenges make answers more right, not more flip-floppy.

### 2026-09 — The gate that wasn't running

**Defect:** An enforcement hook appeared fully configured: on disk, wired in the harness config, hundreds of historical gate-log records in the fire log that appeared to indicate activity, but did not establish successful direct-command invocation. During an observed session, dozens of invocation failures were recorded across three hooks and were present in the transcript but not acted on. The execute bit was missing. Tests invoked the scripts through an interpreter, which doesn't require the execute bit; the harness invoked them as direct commands, which does. Two invocation paths, silently diverged.
**Root cause:** "Tested" and "tested via the production invocation path" are different claims. Every test used an interpreter call that bypassed the requirement that would have caught the failure. The missing execute bit is detectable with `ls -l` — the deeper gap was that no routine confirmed the scripts could be invoked via the direct command path the harness uses.
**Countermeasure:** Before declaring any hook live, invoke it as a direct command with a benign payload and alarm on a launch/permission failure (distinct from a policy denial). Cross-check configuration against disk in both directions. These checks belong in an automated test — not just a design rule.
**Result:** A design rule now prescribes the check, and an automated liveness test implementing Steps 0–3 (direct command invocation, broken-launch fixture, deny test, allow control) was subsequently built and runs against the pre-tool gate. See `incidents/001-configured-is-not-running.md`.
