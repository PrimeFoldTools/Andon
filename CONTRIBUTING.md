# Contributing to Andon

Thanks for taking the time to improve this. Andon is intentionally small: plain
Markdown, stdlib Python, and a bias toward mechanisms people can inspect in a few
minutes.

## What helps most

- Bug reports with the exact command you ran, the hook mode, your Python version,

  and the output you saw.

- False-positive / false-negative examples for `claim_check_hook.py`, especially

  real assistant sentences that should or should not trigger.

- Small docs fixes where a first-time user would get stuck.
- New defect-ledger entries that describe a **reusable failure class** (not a

  private one-off), in the four-part shape:
  defect -> root cause -> countermeasure -> result.
  End it with a `**Verdict:**` and a `**Control:**` line (see "How each entry is
  tracked" at the top of the ledger). A new entry usually starts as
  `needs_validation` or `confirmed`, with status `experimental`.

## Development

Run the local checks before opening a PR:

```bash
python3 -m compileall -q hooks scripts
python3 -m pytest hooks/tests/
python3 scripts/check_ledger.py
```

The hooks should remain stdlib-only. If a change needs a dependency, open an
issue first and explain why the tradeoff is worth it.

## Pull request expectations

PRs are reviewed against the standing check sheet in
[docs/INCOMING_INSPECTION.md](docs/INCOMING_INSPECTION.md).

- Keep changes focused. One bug or doc improvement per PR is perfect.
- Add or update a regression test when hook behavior changes.
- Default enforcement stays gentle: warn first, block only when the operator opts

  in.

- Do not add private project details, local paths, tokens, transcripts, or

  customer data to examples or tests.

## Maintainer note

This is a solo-maintained project, stripped from the system I run daily. I read
issues and PRs, but there are no response-time guarantees.
