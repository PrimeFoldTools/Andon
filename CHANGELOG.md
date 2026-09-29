# Changelog

Notable changes to the andon **starter kit**. Loosely follows
[Keep a Changelog](https://keepachangelog.com/). The kit versions independently
of the doctrine in [`docs/THE_OPERATORS_CODE.md`](docs/THE_OPERATORS_CODE.md).

## v0.1.1 — Claim-check binding

The claim-check hook now binds a fresh verification entry to the specific **claim** and
**session**, closing the self-/unrelated-work/cross-session **alibi** hole (#2, reported by
gcracolici with reproductions). `log_claim.py` stamps `session_id` (from `CLAUDE_CODE_SESSION_ID`)
and a `claim_fingerprint` (content tokens of the claim); the hook requires a fresh, same-session
entry whose fingerprint shares a subject token with the claim. Legacy entries (pre-binding) stay
fail-open and self-expire within one freshness window. The block message now names the verification
required rather than the command that clears the gate.

## v0.1.0 — Initial public release

The starter kit: typed memory templates, the **claim-check** Stop hook (catches a
false "done"), **auto-orient** (loads memory at session start), the wrap/orient
loop, lanes, a starter agent team, ready-made slash commands, the Defect Ledger,
and the full doctrine. Plain Markdown + stdlib Python 3 — no dependencies, no
account, no lock-in. Ships in **warn** mode; promote to **block** when you trust it.
