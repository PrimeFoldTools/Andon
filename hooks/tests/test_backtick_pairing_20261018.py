#!/usr/bin/env python3
"""Regression tests for the code-span exemption in claim_check_hook (audit 2026-10-18).

The old exemption looked for the nearest backtick before a claim and the nearest one
after it (within 200 chars), so the CLOSING tick of one inline span and the OPENING tick
of the next looked like one span around ordinary prose: a done-claim between two code
spans was silently exempted. It also never saw a fence opener more than 200 chars back.

The exemption now pairs backticks the way Markdown does: fenced blocks first, then
inline runs of equal length, left to right, within a line. An unpaired backtick is a
literal character and an unclosed fence is plain text, so neither can hide the rest of
the message.

These import find_claims directly (detector semantics); one test goes through the
Stop-hook subprocess, the real production entry point. Expected answers were checked
against a CommonMark parser (markdown-it-py), except the cases marked deliberately
strict. Some tests are controls that the old code also passes: they pin behaviour the
new pairing must keep, and each new rule has at least one test that fails without it.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claim_check_hook import find_claims  # noqa: E402

HOOK = str(Path(__file__).resolve().parent.parent / "claim_check_hook.py")
FENCE = "```"


def _fires(text):
    return bool(find_claims(text))


# ---- paired inline spans: only the text INSIDE a span is code ----
def test_claim_between_two_inline_spans_fires():
    assert find_claims("I updated `a.py`. The migration is done. Also touched `b.py`.") == ["is done"]


def test_claim_between_spans_in_one_sentence_fires():
    assert find_claims("The `migration.py` script is complete and `tests/` pass.") == ["is complete"]


def test_claim_inside_inline_span_stays_exempt():
    assert not _fires("Set the flag `is done` in config.")


def test_double_backtick_span_with_inner_tick_stays_exempt():
    # ``...`...`` is ONE span (runs pair by equal length), so the inner tick is content.
    assert not _fires("Set ``the `x` flag is done`` in config.")


def test_claim_after_spans_on_the_same_line_fires():
    assert find_claims("Ran `pytest -q` and `ruff check`; the fix is complete.") == ["is complete"]


def test_claim_that_contains_a_code_span_still_fires():
    # The match runs across `parser`; only a match wholly inside code is exempt.
    assert _fires("Fixed the `parser` off-by-one in pagination.")


# ---- unpaired backticks are literal: they never swallow the rest of the text ----
def test_unpaired_tick_after_a_closed_span_does_not_hide_claim():
    # Three ticks: `make` pairs, the third has no partner. The old code paired the
    # closing tick of `make` with the stray one and hid the claim between them.
    assert find_claims("Run `make`. The migration is done. Note: ` is a backtick.") == ["is done"]


def test_unpaired_tick_does_not_pair_across_lines():
    assert find_claims("Use the ` key.\nThe migration is done.\nSee `x`.") == ["is done"]


def test_lone_unpaired_tick_before_claim_fires():
    assert find_claims("Press ` to open the console. The migration is done.") == ["is done"]


# ---- triple backticks inline (not a fence) ----
def test_triple_backtick_inline_span_is_exempt():
    assert not _fires("Wrap it as ```the build is complete``` in the docs.")


def test_triple_backtick_inline_span_at_line_start_is_exempt():
    # A line that starts with ``` but has a backtick in its info string is not a fence
    # opener; it is an inline span of run length 3.
    assert not _fires("```the build is complete``` is the phrase to grep for.")


def test_claim_after_triple_backtick_inline_span_fires():
    assert find_claims("Ran ```make```. The build is complete.") == ["is complete"]


def test_runs_of_different_length_do_not_pair():
    # ``` (3) and ` (1) are not a pair: both are literal, so the claim between them is prose.
    assert find_claims("Typed ``` then the migration is done, then `.") == ["is done"]


def test_double_backtick_span_closes_on_double_not_single():
    # ``x ` y`` is one span and `z` another; the claim between them is prose. Pairing the
    # opening `` with the lone ` would make `` and the migration is done. ` a span.
    assert find_claims("Use ``x ` y`` and the migration is done. `z` too.") == ["is done"]


# ---- fenced code blocks ----
def test_claim_inside_fence_is_exempt():
    assert not _fires(f"Output:\n{FENCE}\nthe build is complete\n{FENCE}")


def test_claim_between_two_fences_fires():
    msg = f"See:\n{FENCE}\nx\n{FENCE}\nThe migration is done.\n{FENCE}\ny\n{FENCE}"
    assert find_claims(msg) == ["is done"]


def test_claim_deep_inside_a_long_fence_is_exempt():
    # The old 200-char window never saw an opener this far back and fired on code.
    filler = "\n".join(f"line {i}: nothing to see here" for i in range(20))
    assert not _fires(f"Log:\n{FENCE}text\n{filler}\nINFO: the build is complete\n{FENCE}")


def test_fence_with_info_string_and_list_indent():
    msg = (f"1. Step one\n   {FENCE}bash\n   echo the build is complete\n   {FENCE}\n"
           f"The migration is done.")
    assert find_claims(msg) == ["is done"]


def test_longer_closing_fence_closes_a_shorter_opener():
    assert not _fires(f"{FENCE}\nthe build is complete\n````\nNothing else.")


def test_inner_shorter_fence_does_not_close_a_longer_one():
    msg = f"````\n{FENCE}\nthe build is complete\n{FENCE}\n````\nNothing else."
    assert not _fires(msg)


def test_inline_ticks_inside_a_fence_do_not_pair_with_prose():
    # A stray tick inside a fence is code content; it must not pair with a tick in the
    # prose after the fence and hide the claim between them.
    msg = f"{FENCE}\necho ` odd\n{FENCE}\nThe migration is done. See `x`."
    assert find_claims(msg) == ["is done"]


def test_unclosed_fence_does_not_swallow_the_rest():
    assert find_claims(f"{FENCE}python\nThe migration is done. See `x`.") == ["is done"]


def test_claim_after_a_long_fence_closes_fires():
    # Guards the other side of the long-fence exemption: the fence must END at its
    # closer, so prose after a long block is still read.
    filler = "\n".join(f"line {i}: the build is complete" for i in range(40))
    assert find_claims(f"Log:\n{FENCE}\n{filler}\n{FENCE}\nThe migration is done.") == ["is done"]


# ---- fence indentation follows Markdown (review round, 2026-10-18) ----
def test_over_indented_fence_marker_is_not_a_fence():
    # Four spaces is indented code, not a fence opener (Markdown allows 0-3). Read as a
    # fence, it hid twenty lines of prose and the claim below them.
    msg = "    ```\n" + "ordinary prose\n" * 20 + "The build is complete.\n    ```"
    assert find_claims(msg) == ["is complete"]


def test_tab_indented_fence_marker_is_not_a_fence():
    # A leading tab counts as four columns, so this is not a fence opener either.
    assert find_claims("\t```\nThe build is complete.\n\t```") == ["is complete"]


def test_fence_indented_four_inside_a_numbered_list_item_is_a_fence():
    # "10. " puts the item's content at column 4, so a fence indented 4 belongs to it.
    msg = (f"10. Run:\n    {FENCE}\n    echo the build is complete\n    {FENCE}\n"
           f"The migration is done.")
    assert find_claims(msg) == ["is done"]


def test_fence_inside_a_nested_list_item_is_a_fence():
    msg = f"- outer\n  - inner:\n    {FENCE}\n    the build is complete\n    {FENCE}\n"
    assert not _fires(msg)


def test_over_indented_marker_cannot_open_a_fence_closed_below():
    # Line 1 is indented code, line 2 prose, line 3 opens a NEW (unclosed) fence.
    assert find_claims("    ```\nThe build is complete.\n```") == ["is complete"]


def test_over_indented_closer_is_fence_content():
    # Four spaces cannot close a fence, so the fence runs on and holds the claim.
    assert not _fires(f"{FENCE}\nthe build is complete\n    {FENCE}\nThe migration is done.\n{FENCE}")


def test_fence_opened_on_a_list_marker_line_is_a_fence():
    msg = f"1. {FENCE}bash\n   echo the build is complete\n   {FENCE}\nThe migration is done."
    assert find_claims(msg) == ["is done"]


def test_fence_opened_on_a_marker_line_ends_with_its_item():
    assert find_claims(f"- {FENCE}\n  log\nThe build is complete.\n  {FENCE}") == ["is complete"]


def test_list_item_that_ends_inside_a_fence_ends_the_fence():
    # The unindented line leaves the list item, which closes the item's fence. The
    # indented marker after it cannot close a fence that has already ended.
    msg = f"- item:\n  {FENCE}\n  log\nThe build is complete.\n  {FENCE}"
    assert find_claims(msg) == ["is complete"]


# ---- inline spans may cross ONE line break inside a paragraph ----
def test_inline_span_wrapped_across_one_line_break_is_exempt():
    assert not _fires("Example: `the build\nis complete`.")


def test_wrapped_span_pairs_before_spans_on_the_next_line():
    # Markdown pairs left to right: the tick on line 1 closes on line 2's FIRST tick.
    assert not _fires("Example: `the build\nis complete` and `x`.")


def test_lazy_continuation_line_of_a_list_item_can_close_a_span():
    assert not _fires("- item `a\nb is done` rest")


def test_span_does_not_cross_into_the_next_list_item():
    # Each list item is its own paragraph, so the tick in item 1 is a literal.
    assert find_claims("- Set `flag\n- The migration is done. See `x`.") == ["is done"]


def test_span_does_not_cross_out_of_a_heading():
    assert find_claims("# Title `a\nThe migration is done `x` hmm") == ["is done"]


def test_span_does_not_cross_a_blank_line():
    assert find_claims("Set `flag\n\nThe migration is done. See `x`.") == ["is done"]


def test_tick_on_the_previous_line_pairs_before_ticks_on_this_line():
    # Markdown pairs line 1's `` with the FIRST `` on line 2; the last `` is a literal,
    # so the claim between them is prose. Pairing within line 2 first hid it.
    msg = "x\n``\n``" + "y" * 230 + "The migration is done." + "y" * 20 + "``"
    assert find_claims(msg) == ["is done"]


def test_span_across_two_line_breaks_does_not_pair():
    # Deliberately strict (Markdown would pair these): a span may cross one line break,
    # not two, so a stray tick cannot hide whole lines of prose.
    assert find_claims("Use `the build\nwas slow and\nis complete` now.") == ["is complete"]


def test_a_pair_two_lines_apart_does_not_shift_later_pairs():
    # Markdown pairs line 1's `` with line 3's, so line 4's `` is a literal and the claim
    # is prose. The one-line limit alone would skip line 1's tick and pair lines 3-4.
    assert find_claims("ab ``x\nword\n``\nThe migration is done.``") == ["is done"]


def test_blank_line_starts_a_new_paragraph_for_pairing():
    # Line 1's `` is alone in its paragraph (a literal); lines 3-4 pair, so the claim
    # is code. A tick must never pair across a blank line.
    assert not _fires("ab ``x\n\n``\nThe migration is done.``")


def test_numbered_line_inside_the_same_list_item_continues_its_paragraph():
    # "2." cannot interrupt a paragraph in the same item, so the span closes on line 2.
    assert not _fires("- a `x\n  2. the migration is done` y")


def test_numbered_line_outside_the_item_starts_a_new_list():
    # Out of the item's column the line is lazy, and then any numbered item starts a list.
    assert find_claims("- a `x\n2. The migration is done. `y") == ["is done"]


def test_five_columns_after_a_marker_is_indented_code_not_a_paragraph():
    assert find_claims("-      ``x\nThe migration is done.``") == ["is done"]


def test_tilde_fence_line_ends_the_paragraph():
    # "~~~ `x" opens a ~~~ fence (its info may hold ticks), so the line-1 tick is literal.
    assert find_claims("Run `a then the migration is done.\n~~~ `x") == ["is done"]


def test_fence_in_a_quote_in_a_list_item_is_not_paragraph_text():
    assert find_claims("2. > ```x\nThe migration is done.``` x") == ["is done"]


# ---- the binary-search speedups give the same answers as the scans they replace ----
def test_token_set_attribution_matches_a_full_scan():
    import random
    import claim_check_hook as h
    rng = random.Random(3)
    words = ["The migration is done.", "`a`", "x.py", " ", "\n", "Fixed the parser.",
             "Done.", "ok", "the build is complete", "?"]
    for _ in range(400):
        msg = "".join(rng.choice(words) for _ in range(rng.randint(1, 12)))
        frags = h._fragment_spans(msg)
        want = []
        for _p, s, e in h._iter_claim_matches(msg):
            toks = h._content_tokens(" ".join(msg[a:b] for a, b in frags if a < e and b > s))
            want.append(toks or h._content_tokens(msg))
        assert h._claim_token_sets(msg) == (want or [h._content_tokens(msg)]), repr(msg)


def test_blockquote_checker_matches_a_backward_scan():
    import random
    import claim_check_hook as h

    def scan(text, m_start):     # the per-call scan it replaced
        i, saw = text.rfind("\n", 0, m_start) + 1, False
        while i < m_start and text[i] in "> \t":
            saw = saw or text[i] == ">"
            i += 1
        return saw
    rng = random.Random(7)
    for _ in range(2000):
        t = "".join(rng.choice(["\n", ">", " ", "\t", "a", "> ", "x y"])
                    for _ in range(rng.randint(0, 20)))
        is_quoted = h._blockquote_checker(t)
        assert [is_quoted(i) for i in range(len(t) + 1)] == \
               [scan(t, i) for i in range(len(t) + 1)], repr(t)


# ---- cost stays linear on a huge message (a Stop hook has a timeout) ----
def test_many_code_spans_and_claims_stay_fast():
    import time
    from claim_check_hook import _claim_token_sets
    msg = "`a` is done. " * 23000      # ~300 KB, 23,000 spans and 23,000 claims
    t0 = time.monotonic()
    assert find_claims(msg) == ["is done"]
    assert len(_claim_token_sets(msg)) == 23000
    assert time.monotonic() - t0 < 5.0


# ---- end to end through the Stop hook ----
def test_hook_blocks_claim_between_two_inline_spans(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_text(json.dumps({
        "type": "assistant",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"content": [{"type": "text", "text":
                                 "I updated `a.py`. The migration is done. Also touched `b.py`."}]},
    }) + "\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAIM_CHECK") and k != "CLAUDE_CODE_SESSION_ID"}
    env.update({"CLAIM_CHECK_ENFORCE_MODE": "block",
                "CLAIM_CHECKS_LOG_PATH": str(tmp_path / "log.jsonl")})
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({"transcript_path": str(p)}),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out.get("decision") == "block"
    assert "is done" in out["reason"]
