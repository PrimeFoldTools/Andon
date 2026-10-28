# Stand on these shoulders

Andon is an operating *discipline*, not a platform. It sits on top of other people's tools and ideas — most of what makes a real AI OS work is theirs, not mine. Credit where it's due, and a starting reading list if you want to go deeper.

> See also the README's [“Why this, and not the 20 other memory repos”](../README.md#why-this-and-not-the-20-other-memory-repos) — the honest, named comparison to the lighter memory tools.

## The ideas

- **Toyota Production System / Lean** (Taiichi Ohno and the people who built it) — the whole spine: *andon*, *poka-yoke*, *jidoka*, *kaizen*, standard work. This project is mostly TPS pointed at agents.
- **Andrej Karpathy** — the "LLM as an operating system" framing, and the [autoresearch](https://github.com/karpathy/autoresearch) loop (an English objective + a runner that keeps improvements, discards regressions). If you want an overnight self-improvement loop, start there.
- **Anthropic — Claude Code** — the harness this is written against (hooks, settings, the memory/instructions model). The patterns port to other harnesses; the specifics here are Claude Code's.

## The control lifecycle (Verdict and Control lines in the Defect Ledger)

- **[Sigma rule specification](https://github.com/SigmaHQ/sigma-specification)** (SigmaHQ) — the status ladder (`experimental` / `test` / `stable` / `deprecated`), the `related` link types (`derived` / `obsolete` / `merged` / `renamed`) and the `modified` date. Detection engineers already manage rules that age; Andon borrows their words. Left out: Sigma's `similar` link, its `unsupported` status and `falsepositives` (the Result line already states what a control misses).
- **[OSV schema](https://github.com/ossf/osv-schema)** (OpenSSF) — `withdrawn`: a record that turned out wrong stays published and is marked, never deleted. OSV stores it as a timestamp; Andon uses it as a status value. OSV's `aliases` was left out, because the `D-NNN` id plus a `renamed` link covers it.
- **[todo_or_die](https://github.com/searls/todo_or_die)** (Justin Searls) — a TODO with a date that breaks the build when the date passes. That is `review_by`.
- **[security-audit-skill](https://github.com/cloudflare/security-audit-skill)** (Cloudflare) — the three verdicts: `confirmed` (complete evidence), `needs_validation` (names the exact unresolved fact), `rejected` (disproved, kept on record).

## A Lean neighbour

- **[poka-yoke](https://github.com/rainmanjam/poka-yoke)** (rainmanjam) — Shigeo Shingo's mistake-proofing for agents: design skills, a hazard scanner and installable devices, aimed at the code and systems an agent builds. Andon starts from the agent's own verified failures, and its ledger records why each control exists, how far it is trusted, and when it can retire.

## The tools (you'll likely want some of these for Layer 4)

- **[rtk](https://github.com/rtk-ai/rtk)** — compresses/filters shell output before it hits your context. The fastest token win for a heavy agent setup.
- **[graphify](https://github.com/safishamsi/graphify)** — turns a repo into a queryable knowledge graph; far fewer tokens per question than reading raw files.
- **[claude-mem](https://github.com/thedotmack/claude-mem)** — auto-captures session activity and re-injects relevant context. A heavier, automated cousin of the memory layer here.
- **[claude-reflect](https://github.com/BayramAnnakov/claude-reflect)** — auto-captures your corrections into a rule file. If memory + a mistakes log is all you want, start here (it's the honest alternative named in the README).
- **[halo](https://github.com/context-labs/halo)** — a self-improving agent harness (trace → find failure modes → patch the harness → repeat). The pattern behind a real learning loop.
- **[Agent Skills for Context Engineering](https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering)** (Murat Çankoylan) — multi-agent patterns, context-degradation taxonomy, filesystem-as-context. Excellent companions to the lanes + wrap layers.

If you build something on top of this, or you maintain a tool that belongs here, open a PR — this list should grow.
