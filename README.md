# Andon

**Stop fixing the same AI mistake twice.**

A Lean *quality system* for AI-assisted work — operators turn recurring defects into countermeasures. Built by a manufacturing operator, for people who actually ship.

![License: MIT](https://img.shields.io/badge/License-MIT-black.svg) ![Kit: v1](https://img.shields.io/badge/kit-v1-blue.svg) ![Tests](https://github.com/PrimeFoldTools/andon/actions/workflows/tests.yml/badge.svg)

> *Andon* is the cord on a Toyota line you pull to stop production when something's wrong. This is that cord for AI work: catch the defect, investigate it, and add a countermeasure.

*Not affiliated with [Andon Labs](https://andonlabs.com) (the AI-agent evaluation company). Same Lean word, different project — this is a solo, open-source operating kit.*

---

## The problem

If you work inside an AI agent every day, you know the failure modes:

- It **forgets** what you decided last week.
- It **repeats** mistakes you already corrected.
- It says **"done"** when it isn't.
- Projects **drift** — two sessions stomp the same file, context evaporates, you re-explain the same thing.

Most "AI tips" make the *output* a little better. Recurring defects also need explicit checks and follow-through.

**The fix isn't a better prompt. It's an operating discipline — borrowed from a factory floor.** Use the same discipline in AI work: record a defect, investigate its cause, and add a countermeasure. Where practical, add a mechanical check and test both the failure and a legitimate passing case. Checks have limits; keep those visible.

The aim: **retain what you learned and check whether the countermeasure works.**

---

## Quickstart — your first win in 5 minutes (no code)

The smallest useful piece is a structured memory file — so you can come back to a project after three months and your agent knows exactly where you left off, instead of rebuilding context from scratch.

0. Clone this repo first (you'll copy a couple of files out of it):

   ```bash
   git clone https://github.com/PrimeFoldTools/andon.git
   ```

1. In your project, make a memory folder and an index:

   ```bash
   mkdir -p memory
   cp /path/to/andon/templates/MEMORY.md.template memory/MEMORY.md   # path to where you cloned andon
   ```

2. Add 2–3 lines to `memory/MEMORY.md` — the durable facts your agent keeps forgetting (the stack, the rule, the decision).
3. Point your agent at it (e.g. in your `CLAUDE.md`: *"Read `memory/MEMORY.md` at the start of every session."*).

That's Layer 1. The payoff shows up the *next* time your agent starts — it reads the file instead of re-asking, so there's nothing flashy to watch right now. Want an instant, visible win instead? Run the 30-second hook demo below. Full path in [QUICKSTART.md](QUICKSTART.md).

---

## See it work — the andon cord catching a false "done"

The claim-check hook detects some completion phrases and checks their verification entries. In block mode, a failed check returns a block decision.

The example below runs the hook in **warn** mode with no verification entry. Its response asks for evidence; it does not establish whether the agent performed the work. Promote to **block** only after checking its behavior on your work. See [HOOK_INSTALL.md](HOOK_INSTALL.md).

The agent writes its own verification entry. Token overlap can accept unrelated work or a fabricated entry; the known limits below describe these cases.

### Reproduce it in 30 seconds

From inside the cloned repo — no install, no config:

```bash
rm -f /tmp/andon_claim_checks_none.jsonl

printf '{"type":"assistant","timestamp":"%s","message":{"content":"the migration is complete and the tests are fixed"}}\n' \
  "$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')" > /tmp/t.jsonl

echo '{"transcript_path":"/tmp/t.jsonl"}' \
  | CLAIM_CHECK_ENFORCE_MODE=warn CLAIM_CHECKS_LOG_PATH=/tmp/andon_claim_checks_none.jsonl python3 hooks/claim_check_hook.py
```

The response contains `continue: true` and a `systemMessage` asking for claim-specific verification. Run `python3 -m pytest hooks/tests/` to check the hook suite.

*Not ready to install a hook? Layer 1 above — a plain memory file, no code — is the on-ramp. Start there and climb when you feel the friction.*

---

## The Defect Ledger — accumulated, not invented

The runnable proof is the 30-second demo above. The companion evidence is the *record*: [`DEFECT_LEDGER.md`](DEFECT_LEDGER.md) logs real defects one at a time — **defect → root cause → countermeasure → result** — where the countermeasure is a hook or test that makes the whole class hard to repeat, not a note that asks you to remember. Accumulated, not invented. Read a few entries; you'll recognize your own week.

---

## Why this, and not the 20 other memory repos

Be honest: "give your agent memory + a mistakes log" is a crowded idea in 2026. Some tools even auto-capture your corrections into a rule file (e.g. [claude-reflect](https://github.com/BayramAnnakov/claude-reflect)). If you just want memory, use one of those — they're good.

andon is a different thing: **a complete operating discipline, not a memory tool** — informed by the Toyota Production System's practices for investigating defects and testing countermeasures.

- **It's the whole line, not just memory.** Memory + the wrap/orient loop + the claim-check cord + lanes + a starter agent team + the doctrine — one opinionated system with a 5-minute on-ramp.
- **A defect closes with a *countermeasure*, not a note.** Writing the mistake down isn't the fix — the fix is a hook or test that makes the whole class harder to repeat (*poka-yoke*). That's the factory difference between "we'll try to remember" and "the system catches it next time." The [Defect Ledger](DEFECT_LEDGER.md) is where you see it.
- **It's from someone who ran the floor.** Not a framework reasoned from first principles — 50-year-old manufacturing discipline, ported to agents by someone who lived it.

If that frame resonates, the rest is the proof. If it doesn't, one of the lighter memory repos will serve you better — no hard feelings. (Full credit + a reading list of the tools and ideas andon stands on: [stand-on-these-shoulders.md](docs/stand-on-these-shoulders.md).)

---

## What's inside

```text
andon/
├── README.md            ← you are here
├── QUICKSTART.md        ← the 5-minute first win, step by step
├── DEFECT_LEDGER.md     ← real defects → countermeasures → results (the proof)
├── HOOK_INSTALL.md      ← install the two hooks (warn-first, with a "turn it off")
├── TROUBLESHOOTING.md   ← when something doesn't fire / fires too much
├── AGENTS.md            ← point your AI agent at this and it self-installs andon
├── CONTRIBUTING.md      ← how to report issues, PRs, and hook examples
├── SECURITY.md          ← local-first security model + responsible reporting
├── .github/workflows/   ← CI for the Python hooks
├── templates/           ← drop-in: CLAUDE.md · MEMORY.md · LANES.md · CONTEXT.md · lane · skill · wrap
├── hooks/               ← claim_check_hook.py (catches false "done") · auto_orient.py (loads memory at start) · log_claim.py · tests
├── commands/            ← ready-made slash commands: /wrap · /orient · /log-mistake
├── agents/              ← a starter team: researcher · auditor · memory-steward · builder · chief-of-staff
├── scripts/             ← memory_rotate.py (keeps the index from bloating)
├── examples/            ← a FILLED-IN sample project (not empty placeholders)
└── docs/
    ├── THE_OPERATORS_CODE.md        ← the full doctrine: 11 laws + 5 patterns
    ├── INCOMING_INSPECTION.md       ← the check sheet incoming PRs go through
    ├── integrations.md              ← wiring to Obsidian / vector search / Notion
    └── stand-on-these-shoulders.md  ← the tools + repos this builds on
```

Everything is plain Markdown + a few stdlib-only Python scripts (two hooks + a helper). No dependencies, no account, no lock-in.

---

## The four layers — take only what you need

Nothing past Layer 1 is mandatory. Climb when you feel the friction the next layer fixes.

| Layer | You add | Time | Fixes |
|---|---|---|---|
| **1 — Memory** | `MEMORY.md` + a typed memory folder | 5 min | The agent forgetting your project |
| **2 — Instructions** | `CLAUDE.md` + a Mistakes Log | 20 min | Repeating corrected mistakes |
| **3 — The hooks** | claim-check (catches false "done") + auto-orient (loads memory at start) | 30 min | False "done" + starting amnesiac |
| **4 — Operator system** | the agent team · lanes · commands · integrations | when you run parallel sessions | Drift + collisions at scale |

---

## Known limits — read before you trust it

andon is deliberately small and honest about what it does *not* do:

- **Correspondence, not proof.** The claim-check hook checks a detected "done" for a *recent, same-session* verification entry whose subject overlaps the claim — not merely that some entry exists. That blocks the specific bypasses previously demonstrated (the self-, unrelated-work, and cross-session repros in issue #2), but it does **not** establish that verification actually happened. The match is topical token overlap: unrelated work that shares a word with the claim can still clear it; a fabricated same-subject entry passes — the agent writes its own log; a subjectless claim ("Done.") falls back to a weaker same-session freshness floor; a log holding only legacy (pre-binding) entries keeps the old freshness-only behavior. It raises the cost of a false "done" from zero to leaving a matching line on the record — a forcing function, not a guarantee.
- **Detection is intentionally conservative.** The regex catches common closure forms; some real done-claims won't trigger until you tune `COMPLETION_VERBS` to your writing style. It errs toward missing a claim over false-blocking plain English — and it catches the habitual over-claim, not an agent deliberately paraphrasing around a regex it can read.
- **It's a Stop hook, not a PreToolUse hook.** It checks at turn-end, not before a tool runs.
- **It's only as good as the `log_claim.py` discipline around it.** Missing evidence can trigger a warning or block; an agent-written entry still needs scrutiny.
- **Start in `warn` mode.** Promote to `block` only after it behaves well on your work.

---

## Lean → builder translation

The doctrine is Toyota Production System applied to agents. You don't need the vocabulary to use it — but here's the map:

| Lean / TPS | In plain terms | Where it shows up here |
|---|---|---|
| **Andon** | stop the line when a defect appears | the claim-check hook halting a false "done" |
| **Poka-yoke** | mistake-proofing — make the error harder to repeat, don't rely on memory | hooks > "remember to…" |
| **Kaizen** | a countermeasure for every defect | the Mistakes Log + Defect Ledger |
| **Standard work** | one documented best way | the templates + memory schema |
| **Jidoka** | automated defect detection | the Stop / PreToolUse hooks |
| **Genchi genbutsu** | go and see — don't trust the report | verify the real result, not the log |

---

## The doctrine

The full thinking — 11 laws + 5 patterns + one worked mistake-to-countermeasure arc — is in **[docs/THE_OPERATORS_CODE.md](docs/THE_OPERATORS_CODE.md)** (the doctrine carries its own version — currently v3; this starter kit is v1, and they version independently). Read it when you want the *why*; the templates above are the *what*, and you can start without it.

---

## Who made this / staying in touch

I came up in manufacturing — 15 years, up to production manager — where the discipline was Lean and Six Sigma: you investigate why a defect occurred and put a countermeasure in place. Now I run my own work on a fleet of AI agents, and when the same mistakes kept recurring I ported that discipline to them. This repo is that system, stripped of my private work — it stands on a lot of [other people's tools](docs/stand-on-these-shoulders.md).

If a pattern here saves you a session, I'd like to hear what you stripped, kept, or added — open an issue. More of what I build is at **[github.com/PrimeFoldTools](https://github.com/PrimeFoldTools)**.

**Status:** Stripped from the system I run daily, so it gets fixed when it breaks. Solo-maintained — issues and PRs welcome (I read them), no enterprise SLAs. Fork freely.

*MIT licensed. Free. Adapt it to your own work.*
