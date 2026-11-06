# Privacy

andon runs only on your own machine. It collects no data and sends nothing anywhere.

- **No network calls.** The hooks and scripts use the Python standard library and never open a connection.
- **No accounts, no telemetry, no analytics.**
- **What it writes, all local:**
  - `~/.claude/state/claim_checks/log.jsonl`, the claim-check log you write with `log_claim.py`. Override the path with `CLAIM_CHECKS_LOG_PATH`.
  - Small once-per-session marker files in a private per-user temp folder (`andon-auto-orient-<uid>`, mode 0700). They are removed after 7 days.
- **What your agent writes when you run `wrap`:** your own `memory/` files. The `wrap` command is instructions for the agent; andon's code does not write them.
- **What it reads:** the agent's session transcript and final message (to check done-claims), and your `memory/MEMORY.md`.

Delete those files at any time; andon keeps no other copy.

Questions: open an issue at https://github.com/PrimeFoldTools/Andon/issues.
