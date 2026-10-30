# Changelog

Notable changes to the andon **starter kit**. Loosely follows
[Keep a Changelog](https://keepachangelog.com/). The kit versions independently
of the doctrine in [`docs/THE_OPERATORS_CODE.md`](docs/THE_OPERATORS_CODE.md).

## Unreleased

- **Codex CLI plugin.** `codex plugin marketplace add PrimeFoldTools/andon`, then
  `codex plugin add andon@primefold-tools`, then trust the two hooks in `/hooks`. Adds
  `.codex-plugin/plugin.json` (it points at the same `hooks/hooks.json`; Codex reads the
  existing `.claude-plugin/marketplace.json`) and a `log-mistake` skill in `codex/skills/`.
  Codex turns `commands/orient.md` and `commands/wrap.md` into skills by itself. Two small
  compatibility fixes, tested against hook input captured from real `codex exec` runs
  (codex-cli 0.153.4): `log_claim.py` stamps the session from `CODEX_SESSION_ID` /
  `CODEX_THREAD_ID` when `CLAUDE_CODE_SESSION_ID` is unset (before, every Codex log entry
  was session-less and could never clear a claim), and in log evidence mode the Stop hook
  checks `last_assistant_message` even when `transcript_path` is null (Codex allows a null
  path; before, the check was skipped). Claude Code behaviour is unchanged: it always sends
  a transcript path, and `CLAUDE_CODE_SESSION_ID` still wins.

- **Claude Code plugin + marketplace.** The repo is now an installable plugin and a
  single-plugin marketplace: `/plugin marketplace add PrimeFoldTools/andon`, then
  `/plugin install andon@primefold-tools`. Adds `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, `hooks/hooks.json` (the claim-check Stop hook in warn
  mode and the auto-orient UserPromptSubmit hook, both run from `${CLAUDE_PLUGIN_ROOT}`), and
  `bin/andon-log-claim`, a wrapper on the Bash `PATH` that runs `hooks/log_claim.py`. The five
  agent briefs gain `name` / `description` frontmatter so they load as plugin agents; their
  text is unchanged. The hook files are unchanged. The manual install still works as before.
  The manifest version stays at 0.1.1 until a release is cut.

- **claim-check: short closers and the final message.** Short whole-sentence done-claims
  are now detected: "All fixed.", "All done.", "Merged.", "Pushed.", "Done and tested.",
  "That's fixed.", "Should be fixed now.", "Works now.", "Tests pass.", "Good to go.",
  "Ready for review." and similar (`_SHORT_CLOSERS`). Each must start a sentence and end
  it, so "If all tests pass, we can merge." and "Tests pass when X is set." stay quiet.
  "Fixed it." / "I fixed it." now match too. On a 42-phrase survey, detection went from
  10 to 41 claims, with 1 of 42 near-misses firing before and after (the same one). The
  new closure words (merged, pushed, deployed, committed, tested, works, pass/passes/
  passed/passing) are stopwords in both files, so they never bind a claim. The hook now
  checks the final message Claude Code passes on stdin (`last_assistant_message`) when it
  is a string, and reads the transcript only when it is not. Without it, a stale newest
  transcript entry in `tool`/`both` mode is re-read for up to 2 s and then fails closed;
  before, it passed silently. `log` mode is unchanged. See HOOK_INSTALL.md, "Which message
  is checked".

- **Defect Ledger: verdict and control lifecycle.** Each entry now ends with a `**Verdict:**`
  line (`confirmed` / `needs_validation` / `rejected`, each with a reason) and a `**Control:**`
  line (`id`, `status` = `experimental` / `test` / `stable` / `deprecated` / `withdrawn`,
  `review_by`, `modified`, optional `related`). `scripts/check_ledger.py` (stdlib only) fails when
  a value is invalid or an active control's `review_by` date has passed, is before its `modified`
  date, or is more than 365 days after it; lines inside a code fence are not read as an entry's
  Verdict or Control line, and an unclosed fence fails. The test suite runs it
  on the real ledger, so CI goes red when a review lapses. Retired controls stay on record. All
  19 existing entries are backfilled: 6 confirmed, 13 needs_validation; 8 controls at `test`,
  11 at `experimental`, none `stable` (promotion is the maintainer's call). The first review
  dates are 2027-01-27 (experimental) and 2027-04-27 (test). Borrowed from the Sigma rule spec,
  the OSV schema, todo_or_die and Cloudflare's security-audit-skill; see
  [stand-on-these-shoulders](docs/stand-on-these-shoulders.md).

- **claim-check: transcript parsing and matcher cost.** JSONL records (the transcript and
  the verification log) are now split on `\n` only. `str.splitlines()` also split on
  U+2028, U+2029 and U+0085, which JSON allows raw inside a string, and Claude Code writes
  U+2028 unescaped; a final message holding one was read as broken records and the hook
  read an older message instead. In `tool`/`both` mode a newest record that is not valid
  JSON now fails closed like an unreadable transcript; `log` mode still skips it. A last
  line with no terminating newline is a record still being written and is skipped in
  every mode, so it cannot block a turn that makes no claim. The
  first three claim patterns use a new sentence-start anchor that is linear on long runs
  of blank lines (16,000 newlines took about 68 s; 50,000 now take about 0.1 s) and gives
  the same match spans as before (a seeded differential test pins this). Claim token sets
  tokenise each sentence fragment once instead of once per claim (4,000 claims in one
  fragment: about 4 s before, under 0.1 s now). No change to which claims are detected.
  No message-length cap was added: the matcher is linear, and a cap that skips text
  would give a claim a place to hide.

- **claim-check: claims are read as displayed.** Unicode format characters (category Cf,
  such as zero-width spaces, joiners, the BOM and soft hyphens) and control characters
  other than newline and tab are removed from the final message before claim matching.
  Before, one of them inside a claim word ("Do<zero-width space>ne.") meant the claim was
  not detected. Code-span, quote and question checks, token binding and the phrases
  shown in the block message all use the same cleaned text. Text without these
  characters is checked exactly as before.

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
