# Installing the hooks

andon ships two hooks: the **claim-check** Stop hook (catches a false "done") and the **auto-orient** UserPromptSubmit hook (loads your memory at session start). The claim-check is the higher-stakes one — start there, in **warn mode**, then add auto-orient. This guide covers both.

The claim-check hook reads your agent's turn-ending message, and if it claims something is "done / shipped / verified" with no verification on record, it nudges (warn) or blocks (block) the turn — the one most likely to frustrate you if installed wrong, so go in **warn mode first**.

Written for **Claude Code**. The concepts map to any harness with turn-end / prompt hooks.

---

## 1. Copy the hook files

From the cloned `andon/` repo root:

```bash
mkdir -p ~/.claude/hooks
cp hooks/claim_check_hook.py ~/.claude/hooks/
cp hooks/log_claim.py        ~/.claude/hooks/
cp hooks/auto_orient.py      ~/.claude/hooks/
```

All stdlib-only Python 3 — nothing to install. Steps 2–5 set up the claim-check hook; the auto-orient hook is at the bottom.

## 2. Register it as a Stop hook — MERGE, don't overwrite

Open `~/.claude/settings.json`. If a `"hooks"` block already exists, **add to its `"Stop"` array** — do not paste a second top-level `"hooks"` key (that produces invalid JSON and silently breaks all your hooks).

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "CLAIM_CHECK_ENFORCE_MODE=warn python3 /ABSOLUTE/PATH/TO/.claude/hooks/claim_check_hook.py"
          }
        ]
      }
    ]
  }
}
```

- Use the **absolute** path (`~` does not expand inside the JSON command string — run `echo ~/.claude/hooks/claim_check_hook.py` to get the real path).
- `CLAIM_CHECK_ENFORCE_MODE=warn` in the command is how you set the mode (a hook spawned by the harness won't see a shell `export`).

## 3. Test that it actually fired

```bash
echo '{"transcript_path":"/dev/null"}' | python3 ~/.claude/hooks/claim_check_hook.py
# → {"continue": true, "suppressOutput": true}
```

If you get that line, it runs. To see it *trigger*, end a turn where your agent says something like "the migration is complete" with no recent log entry — in warn mode you'll see the warning surface; in block mode the turn won't end.

## 4. Wire the log (so the gate is satisfiable)

The hook checks for a recent entry in `~/.claude/state/claim_checks/log.jsonl`. The agent writes that entry by running the helper **after it actually verifies** something. Add this to your `CLAUDE.md` so the agent knows to:

> After you verify a done-claim (ran the test, checked the real output), log it:
> `python3 ~/.claude/hooks/log_claim.py "what I claim" "how I verified it"`

`log_claim.py` creates the log file + its folder on first run — so the first claim won't fail on a missing path.

The check is deliberately simple: **fresh verification exists**, not "the hook
semantically proved the exact claim." That keeps the hook inspectable and
low-friction. If you need stricter behavior, customize the log schema and match
the claim text to the verification text.

## 5. Promote to block when you trust it

Once warn mode stops surprising you (a week or two), change `=warn` to `=block` in the command. Now an unverified "done" actually stops the turn.

## Known limitations

- The claim-check hook verifies that a **fresh verification entry exists** — it does not semantically prove the exact claim. It's a forcing function + audit trail, not cryptographic proof.
- Regex detection is **intentionally conservative**; some real done-claims won't trigger until you tune `COMPLETION_VERBS` to your writing style (see *Tuning false-fires* below).
- The hook is only as good as the **`log_claim.py` discipline** around it — no log entry, no signal.
- **Start in warn mode.** Promote to block only after it behaves well on your work.
- It is a **Stop hook, not a PreToolUse hook** — it checks at turn-end, not before a tool call.

## The second hook: auto-orient (loads memory at session start)

Lower-stakes — it only *adds* context, never blocks — so you can install it right away. Register it as a **UserPromptSubmit** hook (again, MERGE into any existing array):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABSOLUTE/PATH/TO/.claude/hooks/auto_orient.py"
          }
        ]
      }
    ]
  }
}
```

It looks for `memory/MEMORY.md` in your project (override with `AUTO_ORIENT_MEMORY_PATH=/abs/path`) and injects it **once per session** — not every turn, so it doesn't waste context. Turn it off with `AUTO_ORIENT=off`. Fail-safe like the claim-check hook: no memory file, or any error → it injects nothing and never blocks.

Now **wrap** (save at session end) + **orient** (load at session start) form the full loop — your context survives across sessions automatically.

### Both hooks in one settings.json

Most people want both. They go in the **same** `"hooks"` object — `Stop` and `UserPromptSubmit` as sibling keys. Do **not** paste two separate `{"hooks": …}` blocks; that's invalid JSON and silently kills all your hooks.

```json
{
  "hooks": {
    "Stop": [
      { "matcher": "*", "hooks": [ { "type": "command",
        "command": "CLAIM_CHECK_ENFORCE_MODE=warn python3 /ABSOLUTE/PATH/.claude/hooks/claim_check_hook.py" } ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "*", "hooks": [ { "type": "command",
        "command": "python3 /ABSOLUTE/PATH/.claude/hooks/auto_orient.py" } ] }
    ]
  }
}
```

Already have a `"hooks"` block? Add the `Stop` / `UserPromptSubmit` keys *into* it — don't replace it. Validate after: `python3 -c "import json; json.load(open('$HOME/.claude/settings.json'))"`.

## Turning it off

- Per-run: `CLAIM_CHECK_ENFORCE_MODE=off` in the command (silent pass-through).
- Permanently: remove the hook entry from `settings.json`.
- It is **fail-safe by design**: malformed input, a missing transcript, or any error → it passes the turn through. It never blocks because *it* broke.

## Tuning false-fires

If it fires on normal sentences, edit `COMPLETION_VERBS` at the top of `claim_check_hook.py` — remove any verb that's common in your everyday writing. The looser verbs (`functional`, `current`, `applied`, …) ship **off** in `OPT_IN_VERBS`; add them only if your domain needs them. Result-claims ("the tests pass", "CI is green") are deliberately not matched at all — see the ledger entry on why. More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

> Note: a **Stop** hook blocks with `{"decision": "block"}`. A **PreToolUse** hook uses a *different* schema (`{"hookSpecificOutput": {"permissionDecision": "deny"}}`). If you adapt this skeleton into a PreToolUse guard, switch the schema — the wrong one is a silent no-op.
