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

## 6. Optional: require a harness-recorded run (`CLAIM_CHECK_EVIDENCE=tool`)

By default the evidence is the agent's own `log_claim.py` line. The agent being gated writes the record that clears it. Opt-in evidence modes read a channel the agent controls less: the **transcript**, which Claude Code writes as it executes tools. No extra hook to install; add the variable to the same command:

```json
"command": "CLAIM_CHECK_ENFORCE_MODE=warn CLAIM_CHECK_EVIDENCE=tool python3 /ABSOLUTE/PATH/TO/.claude/hooks/claim_check_hook.py"
```

| `CLAIM_CHECK_EVIDENCE` | A done-claim is cleared by |
|---|---|
| unset, blank or `log` | a fresh, same-session, subject-matching `log_claim.py` entry (unchanged default) |
| `tool` | a passing verification run the harness recorded in this session's transcript, inside the freshness window. A log line alone does **not** count. |
| `both` | both of the above, checked separately |
| any other value | treated as `both` (strictest), so a typo surfaces as warnings instead of silently weakening the gate |

`warn` / `block` / `off` work exactly as before; the evidence mode only changes what counts as evidence. In `tool` / `both` mode an internal error in the hook, or a transcript it cannot find or read, fails **closed** (a warning in warn mode, a block in block mode; hook input that is not JSON only warns); in the default mode it still fails open, as before.

**What counts as a recorded run.** All of these, from the transcript:

1. A `Bash` tool call (not `Monitor`, not an MCP tool, not a subagent's call, not `run_in_background`) on an `assistant` entry with this session's `sessionId`.
2. Its `input.command` — the command line as the harness recorded it — matches the grammar below.
3. Its result is a `tool_result` on a later `user` entry with the same id, `is_error: false`, `interrupted: false`, and none of the three markers real transcripts put on a result that looks green without being a finished run: `backgroundTaskId` (the call was backgrounded, including auto-backgrounding), `timedOutAfterMs` (it hit the tool timeout), `returnCodeInterpretation` (a nonzero exit reinterpreted as success).
4. The result's timestamp is inside the freshness window: the `CLAIM_CHECK_FRESH_MIN` constant in the hook file (15 minutes; edit the constant to change it — it is not an environment variable).
5. No file change comes after it. A call with this session's `sessionId`, later in the transcript than the passing run, cancels that run's credit until a new passing run follows:
   - an `Edit`, `MultiEdit`, `Write` or `NotebookEdit` call on any file except a doc (`.md`, `.markdown`, `.rst`, `LICENSE`, `NOTICE`, `AUTHORS`, `COPYING`); a doc name that is a symlink to a non-doc file is not a doc. A call with no path counts. `.txt` is not a doc (`requirements.txt`, `CMakeLists.txt`).
   - a `Bash` command, unless every segment is read-only. Read-only means: a verification command; a verification command without a fixing option (`--fix`, `--write`, `--outFile`, …); one of `ls cat head tail wc grep egrep fgrep rg pwd which type stat file du df date echo printf true false test [ diff cmp basename dirname realpath readlink uname whoami id jq cut tr nl column cd set sleep`, called by bare name (not `./bin/cat`) and without an option that writes or runs a program (`--output`, `--out`, `--file`, `--pre`, `--exec`, …); bare `env`; `sed -n Np` / `sed -n N,Mp`; `find` without `-exec`, `-execdir`, `-ok`, `-okdir`, `-delete` or `-fprint*`; or `git` without `-c` and with subcommand `status diff log show rev-parse ls-files ls-tree blame grep describe shortlog fetch add commit push remote tag branch reflog cat-file`. A `PATH=`, `LD_*`, `DYLD_*`, `GIT_*` (and the other denied names) assignment in front makes the segment a change. Redirects must go to `/dev/null`, `/dev/stdout`, `/dev/stderr`, an fd (`>&2`), or an absolute path whose real location (symlinks resolved, no `$`, no `..`) is inside `/tmp`, `/private/tmp`, `/var/folders` or the system temp folder and not inside the session's working directory. `$(…)`, backticks and `<(…)`/`>(…)` outside single quotes, heredocs, subshells, glued operators (`;>`), carriage returns and odd Unicode spaces, and unbalanced quotes always count as changes. `#` is never read as a comment.

   A verification line that changes a file in another segment (`pytest && sed -i …`) earns no credit; saving its own output (`pytest -q > out.txt`) is fine. A verifier that fixes files (`ruff check --fix`) cancels earlier runs but keeps its own credit, since its result describes the fixed code. A run and an edit sent in the same message (parallel tool calls) give the run no credit. A run started before the change does not count either, even if its result lands after it. Any attempt counts, including a failed one and a subagent's (sidechain) one. Not seen: a verification command that writes files as it runs, git hooks run by `git commit` or `git push`, a shell alias or function named like an allowed command, and doc edits your tests depend on.

**The grammar.** The harness records the exit status of the *whole* command line, so the hook credits only lines whose status is the verification's own. Rather than parse arbitrary shell, it accepts exactly this shape and refuses everything else:

```
[set -o pipefail ;]  [cd DIR &&]  [NAME=VALUE ...]  VERIFY [ARGS] [REDIRECTS]  [| FILTER [ARGS] ...]  [&& COMMAND [ARGS] ...]
```

- `set -o pipefail` may be written `set -euo pipefail` (any of `e`, `u`, `x` before the `o`) and may end in `;` or `&&`. It must come first. A pipe is accepted only when it does.
- `cd DIR &&` — one directory, not starting with `-`.
- `NAME=VALUE` — leading assignments, except ones that change the shell, the loader or the verifier's options: `PATH`, `BASH_ENV`, `ENV`, `IFS`, `ZDOTDIR`, `SHELLOPTS`, `BASHOPTS`, `PS4`, `PROMPT_COMMAND`, `CDPATH`, `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTHONSTARTUP`, `NODE_OPTIONS`, `MAKEFLAGS`, `MFLAGS`, `GNUMAKEFLAGS`, `GOFLAGS`, and anything starting `LD_`, `DYLD_`, `NPM_CONFIG_`, `TOX_`, `NOX_`, `RUFF_`, `CARGO_` or `RUSTC_` (case-insensitive), and `SHELL`.
- `VERIFY` — the line's words must start with an allowlisted command: `pytest`, `python -m pytest`, `python -m unittest`, `tox`, `nox`, `npm test`, `npm run test`, `pnpm test`, `yarn test`, `cargo test`, `go test`, `make test`, `make check`, `ctest`, `ruff check`, `mypy`, `flake8`, `tsc`, `eslint`. The first word is compared by basename (`.venv/bin/pytest` counts), and `python3`, `python3.11` count as `python`. Options go *after* the command words: `make -C sub test` is refused; write `cd sub && make test`. Override the list with `CLAIM_CHECK_VERIFY_COMMANDS="make test,npm test"` (comma-separated).
- `ARGS` — plain words. Refused: flags that skip the run or force success, e.g. `--version`, `--help`/`-h`, `--collect-only`/`--co`/`--collectonly` and pytest's other alias spellings (and abbreviations of these for the tools that accept them: pytest, tox, nox, mypy, flake8, unittest, make), `--exit-zero` and `ruff check -e`, `make -i`/`-n`/`-q`/`-t`/`-p`/`-v`/`-f` and their long forms, `npm --if-present`/`--ignore-scripts`, `--suppress-tests-failed-exit-code`, `--passWithNoTests`, `cargo test --no-run`/`--list`, `go test -c`/`-n`/`-list`/`-exec`, `ctest -N`/`--no-tests`, `tsc -v`/`--init`/`--showConfig`/`--listFilesOnly`, `npm --script-shell`/`--usage`, `pytest -o`/`-c` (also with the value attached: `-oaddopts=--co`), tox subcommands like `tox list`/`tox exec`, and any `NAME=value` argument to `make` (`SHELL=true` runs no recipe). The full per-tool lists are `_TOOL_RULES` in the hook.
- `REDIRECTS` — `2>&1`, `> FILE`, `>> FILE`, `>| FILE`, `2> FILE`, `2>> FILE`, anywhere in the verification's own segment (a leading `2>&1 pytest -q` is accepted too).
- `FILTER` — `tail`, `head`, `grep` or `cat`, one or more stages, only after `set -o pipefail`. (`head` and `grep -q` can stop reading early; the verification then dies of SIGPIPE and the run is not credited.)
- `&& COMMAND` — plain commands chained with `&&` after the verification. They run only if everything before them succeeded, so a zero status still means the verification passed.
- Words may use `'single'` quotes (anything but `'`) and `"double"` quotes (no `$`, backtick, `\` or `!` inside). Unquoted words use only letters, digits and `_ - . / : = , @ % + ~`. Glob characters `* ? [` must be quoted: the shell expands them before the verifier runs, so a file named `--collect-only` would turn `pytest --co*` into a no-run flag. A quoted glob reaches pytest as a literal pattern, so name a directory or the files you mean instead.

Anything else makes the line uncreditable: `#`, `$`, `(`, `)`, `{`, `}`, `<`, `\`, backticks, `!`, `||`, a lone `&`, `|&`, `&>`, `;` anywhere but right after the leading `set`, a newline or carriage return inside the line, `trap`/`eval`/`exec`/`source` (they are not allowlisted commands, so a line that starts with them never matches). Refusal only withholds credit from an honest run.

Counted:

- `pytest -q`, `python3 -m pytest hooks/tests -q`, `.venv/bin/pytest -x`
- `cd hooks && python3 -m pytest tests -q`, `CI=1 pytest -q 2>&1`, `pytest -q && echo ok`
- `set -o pipefail; pytest -q 2>&1 | tail -3`
- `pytest -k 'a or b' -q`, `pytest -q > out.txt`

Not counted:

- `pytest -q | tail -3` — without `pipefail` a pipe reports `tail`'s status. Add `set -o pipefail;` first.
- `pytest -q; echo done`, `pytest || true`, `true || pytest`, `pytest &`, `echo $(pytest)`, multi-line commands, heredocs.
- `timeout 300 pytest -q`, `npx tsc`, `make -C sub test`, `pytest -pno:cacheprovider` (write `-p no:cacheprovider`) — honest, but not in the grammar.

Tell the agent in `CLAUDE.md`: *"Run verification commands on their own line, in the foreground, unpiped or after `set -o pipefail;`, so the harness records their real exit status."*

How often honest habits miss: across one operator's 1,137 Claude Code session transcripts (top-level `~/.claude/projects/*/*.jsonl`, entries dated 2026-06-27 to 2026-10-16), 1,303 main-thread `Bash` calls mentioned an allowlisted verification command; the grammar credits 8 of them. Of the rest, by first matching reason: 751 were multi-line, 321 piped without `pipefail`, 189 used `$`, backticks, `#`, parentheses, braces, `<`, `\` or `!`, 23 used `||`, and 11 put the command in a position or form the grammar does not accept (`npx tsc` most often). Counted by running the hook's own `_is_verification_command` over every such call; a "mention" includes calls that only wrote or grepped test files, so the count overstates real runs. Expect warnings until the habit changes.

A listed command that runs **zero** tests can still exit 0 and be credited: `python -m unittest` before Python 3.12 ("Ran 0 tests … OK"), and filters that match nothing (`cargo test nomatch`, `go test -run NONE`, `ctest -R nomatch`, `ruff check --ignore ALL`). pytest itself exits 5 when nothing is collected.

**What it does not do** is in the README's *Known limits*: the transcript is still a file the agent's Bash could write, a run is not tied to the claim's subject, and a passing run of a meaningless test still passes.

### Which message is checked

The hook checks the text of the turn's final assistant message. It gets that text in this order:

1. **The hook input.** Claude Code passes the final message to Stop hooks as `last_assistant_message`. Its [hooks reference](https://code.claude.com/docs/en/hooks#stop-input) says the transcript is written asynchronously, so it may not yet hold the final message when the hook runs. When this field is a string, the hook uses it and does not read the message from the transcript. An empty string means the final response had no text, so there is no claim.
2. **The transcript.** If the field is missing (an older Claude Code, or another harness), the hook reads the newest assistant entry in the transcript. If that entry is older than 30 seconds (`STALE_THRESHOLD_S`), it may be the previous turn, because the final message has not been written yet:
   - `log` mode passes the turn, as before.
   - `tool` / `both` mode re-reads the transcript every 0.1 s for up to 2 s (`STALE_RETRY_STEP_S`, `STALE_RETRY_S`). If a fresh entry appears, it is checked. If the entry is still stale after 2 s, the hook fails closed: a warning in warn mode, a block in block mode. A block holds for one turn (`stop_hook_active`).

In `tool` / `both` mode the transcript is still read for the recorded verification run in both cases.

## Known limitations

- The claim-check hook verifies that a **fresh verification entry exists** (or, with `CLAIM_CHECK_EVIDENCE=tool`, a recorded passing run) — it does not semantically prove the exact claim. It's a forcing function + audit trail, not cryptographic proof.
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

It looks for `memory/MEMORY.md` in your project (override with `AUTO_ORIENT_MEMORY_PATH=/abs/path`) and injects it **once per session** — not every turn, so it doesn't waste context. Turn it off with `AUTO_ORIENT=off`. It caps the memory text it injects at 8,000 bytes (a one-line header and a truncation note are added on top); change that with `AUTO_ORIENT_MAX_BYTES=N` (a value that is not a positive whole number falls back to 8,000). Fail-safe: no memory file, or any error → it injects nothing and never blocks.

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
- In the **default evidence mode** (`CLAIM_CHECK_EVIDENCE` unset, blank or `log`) it is **fail-safe by design**: malformed input, a missing transcript, or any error → it passes the turn through. It never blocks because *it* broke.
- With `CLAIM_CHECK_EVIDENCE=tool`, `both`, or any other non-blank value (read as `both`) it **fails closed** instead (section 6). A missing transcript path, a transcript that is missing or unreadable, a newest transcript record that ends in a newline but is not valid JSON (a last line with no newline yet is still being written and is skipped), a final message that is neither in the hook input nor fresh in the transcript after a 2 s wait (see *Which message is checked*), or an internal error gives a warning in warn mode and a block in block mode. Hook input that is not JSON only ever warns. To get unstuck: set `CLAIM_CHECK_ENFORCE_MODE=warn` or `=off`, or remove `CLAIM_CHECK_EVIDENCE`.

## Tuning false-fires

If it fires on normal sentences, edit `COMPLETION_VERBS` at the top of `claim_check_hook.py` — remove any verb that's common in your everyday writing. The looser verbs (`functional`, `current`, `applied`, …) ship **off** in `OPT_IN_VERBS`; add them only if your domain needs them. Result-claims inside a longer sentence ("I added the retry logic and the tests pass", "CI is green again") are deliberately not matched — see the ledger entry on why. Short whole-sentence closers are matched: "Tests pass.", "All fixed.", "Merged.", "That's fixed.", "Works now.", "Good to go." (`_SHORT_CLOSERS` in the hook). Each must start a sentence and end it, so "If all tests pass, merge" and "Tests pass when X is set" stay quiet. More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

> Note: a **Stop** hook blocks with `{"decision": "block"}`. A **PreToolUse** hook uses a *different* schema (`{"hookSpecificOutput": {"permissionDecision": "deny"}}`). If you adapt this skeleton into a PreToolUse guard, switch the schema — the wrong one is a silent no-op.
