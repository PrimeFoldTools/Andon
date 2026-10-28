# Changelog

Notable changes to the andon **starter kit**. Loosely follows
[Keep a Changelog](https://keepachangelog.com/). The kit versions independently
of the doctrine in [`docs/THE_OPERATORS_CODE.md`](docs/THE_OPERATORS_CODE.md).

## Unreleased

- **claim-check: transcript parsing and matcher cost.** JSONL records (the transcript and
  the verification log) are now split on `\n` only. `str.splitlines()` also split on
  U+2028, U+2029 and U+0085, which JSON allows raw inside a string, and Claude Code writes
  U+2028 unescaped; a final message holding one was read as broken records and the hook
  read an older message instead. In `tool`/`both` mode a newest record that is not valid
  JSON now fails closed like an unreadable transcript; `log` mode still skips it. The
  first three claim patterns use a new sentence-start anchor that is linear on long runs
  of blank lines (16,000 newlines took about 68 s; 50,000 now take about 0.1 s) and gives
  the same match spans as before (a seeded differential test pins this). Claim token sets
  tokenise each sentence fragment once instead of once per claim (4,000 claims in one
  fragment: about 4 s before, under 0.1 s now). No change to which claims are detected.
  No message-length cap was added: the matcher is linear, and a cap that skips text
  would give a claim a place to hide.

- **claim-check: opt-in harness-recorded evidence** (`CLAIM_CHECK_EVIDENCE=tool|both`; default
  unchanged). In `tool` mode a done-claim is cleared only by a passing, allowlisted verification run
  (`pytest`, `make test`, …) that Claude Code recorded in this session's transcript inside the
  freshness window — not by the agent's own `log_claim.py` line. A run counts only if its command
  line matches a small fixed grammar whose exit status is the verification's own (HOOK_INSTALL §6);
  everything else, including `| tail` without `pipefail`, `|| true` and `; echo $?`, is refused.
  Runs from another session or a subagent, and failed, interrupted, backgrounded or timed-out runs,
  are not credited. In `tool`/`both` mode an internal hook error fails closed. The transcript is
  still agent-writable; see README *Known limits*. Credit: @w-30x on #2 argued that no content check
  can separate an honest entry from a self-alibi (the lever is the channel), proposed requiring an
  allowlisted run recorded for *this* session inside the window, and named the residual ("one log
  line" → forging harness records). The Assay-generated review @jojig-dao shared on #1 (§2)
  proposed checking claims against a harness-written tool-use log. Both proposed a separate
  PostToolUse recorder; neither the recorder nor a log entry that cites a run is built — this reads
  the transcript instead, and `both` checks the run and the log separately. Reading harness-recorded
  tool calls from the transcript follows the idea in @alex-spataru's Serial-Studio adaptation of this
  hook (`.claude/hooks/claim-check.py`, itself adapted from andon); andon reuses the idea, not the
  code. `scripts/mutation_check_tool_evidence.py` breaks each guard and checks a test fails.
- **claim-check:** better recall on ordinary done-claims (issue #3), as a strictly
  additive change. Sentence-start forms accept a first-person lead-in (`I've implemented
  the…`); `implemented` and `finished` join the standalone list; `in place` and `wired up`
  join `COMPLETION_VERBS`; `live` joins them with its own terminal guard so `live data`
  and `live-streaming` stay quiet; and a subject-noun pattern catches `Task complete!`.
  No example in the accumulated corpus that the detector previously caught stops being
  caught — `hooks/tests/test_claim_corpus.py` asserts the change is additive over that set
  (a corpus-scoped check, not a guarantee for inputs outside it). Evidence-claims (`the tests pass`,
  `CI is green`) are deliberately NOT matched: doing so needs assertion-context
  exemptions, and those exemptions are what suppressed real claims in review.

## v0.1.1 — Claim-check binding

The claim-check hook now binds a fresh verification entry to the specific **claim** and
**session**, closing the self-/unrelated-work/cross-session **alibi** hole (#2, reported by
gcracolici with reproductions). `log_claim.py` stamps `session_id` (from `CLAUDE_CODE_SESSION_ID`)
and a `claim_fingerprint` (content tokens of the claim **and** its verification); the hook requires,
per claim, a fresh same-session entry whose fingerprint shares a subject token with that claim's
sentence. Legacy (pre-binding) entries are honored only until the log has any binding-schema entry
(after that, off for good); the block message now names the verification required rather than the
command that clears the gate. Token overlap is a forcing function, not a proof — see the hook's
`KNOWN LIMITS`.

## v0.1.0 — Initial public release

The starter kit: typed memory templates, the **claim-check** Stop hook (catches a
false "done"), **auto-orient** (loads memory at session start), the wrap/orient
loop, lanes, a starter agent team, ready-made slash commands, the Defect Ledger,
and the full doctrine. Plain Markdown + stdlib Python 3 — no dependencies, no
account, no lock-in. Ships in **warn** mode; promote to **block** when you trust it.
