# AGENTS.md — set up andon (instructions for an AI agent)

If your operator said *"read this repo and set andon up in my project,"* follow this exactly. andon is plain Markdown + stdlib Python 3 — nothing to install. **Ask before overwriting any existing file; show a diff first.** Start at Layer 1; don't install everything unless asked — the value compounds layer by layer.

## Order of operations

**1. Memory (Layer 1) — do this first.** In the project root:
```bash
mkdir -p memory
cp templates/MEMORY.md.template memory/MEMORY.md
```
Then add the operator's durable facts (stack, hard rules, decisions + why) as one-line entries under the index.

**2. Instructions (Layer 2).** Copy `templates/CLAUDE.md.template` → the project's `CLAUDE.md` (or `~/.claude/CLAUDE.md` for global). Fill the Operator profile + Operating Rules; keep the Mistakes Log section. Add this line: *"Read `memory/MEMORY.md` at the start of every session."*

**3. Hooks (Layer 3) — ask first; default to WARN mode.** Copy the three scripts to `~/.claude/hooks/`:
```bash
mkdir -p ~/.claude/hooks
cp hooks/claim_check_hook.py hooks/log_claim.py hooks/auto_orient.py ~/.claude/hooks/
```
Register **both** hooks in `~/.claude/settings.json` using the combined block in `HOOK_INSTALL.md` (section "Both hooks in one settings.json"). **MERGE** into any existing `"hooks"` object — never replace it. Then validate: `python3 -c "import json; json.load(open('$HOME/.claude/settings.json'))"`. Add to `CLAUDE.md`: *"After verifying a done-claim, run `python3 ~/.claude/hooks/log_claim.py \"<claim>\" \"<how verified>\"`."*

**4. Smoke-test the hook:**
```bash
echo '{"transcript_path":"/dev/null"}' | python3 ~/.claude/hooks/claim_check_hook.py
# expect: {"continue": true, "suppressOutput": true}
```
If you see a traceback, **stop and report it** — the hooks are supposed to be fail-safe.

**5. Optional (Layer 4), only if asked.** `templates/LANES.md.template` + `templates/CONTEXT.md.template` for parallel work · `commands/*.md` → `~/.claude/commands/` for slash commands · `scripts/memory_rotate.py` to watch index size · `agents/*.md` are **briefs to paste as a subagent's instructions, not files to install**.

## Rules

- Leave the claim-check hook in **warn** mode. Let the operator promote it to `block`.
- Never overwrite an existing `CLAUDE.md` or `settings.json` without showing the operator the diff.
- The hooks are fail-safe in their default mode (any error → no-op). One exception: with `CLAIM_CHECK_EVIDENCE=tool` or `both`, the claim-check hook fails **closed** instead (a warning in warn mode, a block in block mode; see HOOK_INSTALL §6). A traceback in the smoke test means something's wrong — report, don't proceed.
- Confirm Layer 1 works before moving up. One `MEMORY.md` is already a real win.
