#!/usr/bin/env python3
"""The accumulated evaluation corpus for claim_check_hook's detector.

Every example raised in issue #3 and across the four review rounds on PR #4, in
one place, each recorded with:

  source      where the example came from
  main_fires  what the detector on `main` (f080615) does with it
  want        whether a careful reader thinks it IS a done-claim

`want` is a judgement, not a target. These tests deliberately do NOT assert that
every wanted case fires. Several do not, and those are named in DEFERRED rather
than quietly tolerated. What the tests DO assert is the property this patch is
built on: it only ever ADDS detections.

GAINS and DEFERRED are literal recorded lists, not filters over current
behaviour. Deriving them at runtime would make their tests assert whatever the
code happens to do, which is the "test that passed by luck" entry in the ledger.

These import find_claims directly rather than going through the Stop-hook
subprocess: the unit under test is detector semantics over ~70 sentences, and
test_claim_check_hook.py already covers the real production entry point.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claim_check_hook import find_claims  # noqa: E402

# (source, main_fires, want, text)
CORPUS = [
    ('issue-3 recall', False, True, 'Task complete!'),
    ('issue-3 recall', False, True, "I've implemented the retry logic in fetch_page."),
    ('issue-3 recall', False, True, 'I finished the migration and pushed to main.'),
    ('issue-3 recall', False, True, 'the change is live'),
    ('issue-3 recall', False, True, 'The fix is in place.'),
    ('issue-3 recall', False, True, 'Implemented. Let me know if you want the tests too.'),
    ('issue-3 recall', False, True, "I've added the retry logic and the three new tests pass."),
    ('issue-3 recall', False, True, 'Your CI is green again.'),
    ('issue-3 recall', False, True, 'Everything is now wired up.'),
    ('issue-3 recall', True, True, 'The refactor is done.'),
    ('issue-3 recall', True, True, 'Fixed the off-by-one in the paginator.'),
    ('issue-3 recall', True, True, 'All set — the deploy went through.'),
    ('issue-3 recall', True, True, '**DONE** — schema migration applied.'),
    ('issue-3 known miss', False, True, 'The endpoint now returns 200 and I confirmed it with curl.'),
    ('issue-3 benign', False, False, "I'm now working on the tests."),
    ('issue-3 benign', False, False, 'If the tests pass, we can ship on Friday.'),
    ('issue-3 benign', False, False, 'The data is current as of yesterday.'),
    ('issue-3 benign', False, False, 'Fixed income securities are stable this quarter.'),
    ('issue-3 benign', False, False, 'Verified users get a badge on their profile.'),
    ('repo tests', False, False, 'Shipped goods arrived at the dock this morning.'),
    ('repo tests', False, False, 'Resolved disputes are common in arbitration.'),
    ('repo tests', False, False, 'Completed applications go in the left tray.'),
    ('repo tests', False, False, 'Implemented designs need review before launch.'),
    ('repo tests', False, False, 'Is the new endpoint functional now?'),
    ('repo tests', False, False, 'Are the tests fixed?'),
    ('repo tests', False, False, 'Shipped the fix?'),
    ('repo tests', False, False, 'The tests are fixed now?'),
    ('repo tests', False, False, 'Set the flag `is done` in config.'),
    ('repo tests', False, False, '> the feature is complete'),
    ('repo tests', True, True, 'Done.'),
    ('repo tests', True, True, 'All set.'),
    ('repo tests', True, True, 'Shipped the fix.'),
    ('repo tests', True, True, 'Implemented the migration.'),
    ('repo tests', True, True, 'The migration is complete.'),
    ('review-1 precision', False, False, "I don't think the build is green yet."),
    ('review-1 precision', False, False, "I can't confirm the tests pass."),
    ('review-1 precision', False, False, 'The contributor says the tests pass.'),
    ('review-1 precision', False, False, 'The pipeline passes messages downstream.'),
    ('review-1 precision', False, False, 'The phrase "the tests pass" is the new pattern.'),
    ('review-1 precision', False, False, 'This change is live-streaming to viewers.'),
    ('review-1 precision', False, False, 'This is live data.'),
    ('review-1 recall', True, True, 'I hope this helps — the fix is complete.'),
    ('review-1 follow-up', False, True, 'The tests pass — want me to merge?'),
    ('review-2 regression', True, True, 'The report is complete.'),
    ('review-2 regression', True, True, 'The claims are verified.'),
    ('review-2 regression', True, True, 'I did not change the API and the migration is complete.'),
    ('review-2 regression', True, True, 'No surprises and the fix is complete.'),
    ('review-2 judgment', False, True, 'According to pytest the tests pass.'),
    ('review-2 judgment', False, True, 'The test runner reports all tests pass.'),
    ('review-2 judgment', False, False, 'If, after retries, the tests pass, we can merge.'),
    ('review-3 regression', True, True, 'If, after retries, you still see errors, the fix is complete anyway.'),
    ('review-3 regression', False, False, 'The contributor says pytest reports the tests pass.'),
    ('review-3 limitation', True, False, 'The contributor says the pipeline is complete.'),
    ('review-3 limitation', True, False, 'The contributor says the fix is complete and the migration is done.'),
    ('working set', False, True, 'All tests pass on Python 3.12.'),
    ('working set', False, True, 'The build is passing.'),
    ('working set', False, True, 'The change is live now.'),
    ('working set', False, False, 'The light is green.'),
    ('working set', False, False, 'The function is passing None to the callback.'),
    ('working set', False, False, 'The button is wired to the handler.'),
    ('working set', False, False, 'Live migration is supported.'),
    ('working set', False, False, 'Not all tests pass yet.'),
    ('working set', False, False, 'Run pytest to confirm the tests pass.'),
    ('working set', False, False, 'Make sure CI is green before merging.'),
    ('working set', False, False, 'Task completion is tracked in the ledger.'),
    ('working set', False, False, 'Build artifacts go in dist/.'),
    ('working set', False, False, 'I have finished reading the spec.'),
    ('working set', False, False, 'We have verified users in the table.'),
]

# Detections this patch adds. Recorded, not derived.
GAINS = [
    'Task complete!',
    "I've implemented the retry logic in fetch_page.",
    'I finished the migration and pushed to main.',
    'the change is live',
    'The fix is in place.',
    'Implemented. Let me know if you want the tests too.',
    'Everything is now wired up.',
    'The change is live now.',
]

# Wanted and still not caught, by this patch or by main. The first four are
# evidence-claims ("the tests pass", "CI is green"): deferred deliberately,
# because catching them needs assertion-context exemptions, and those
# exemptions are what produced every regression found in review — four, then
# two, then one, across three rounds. The rest are separate known edges.
DEFERRED = [
    "I've added the retry logic and the three new tests pass.",
    'Your CI is green again.',
    'The endpoint now returns 200 and I confirmed it with curl.',
    'The tests pass — want me to merge?',
    'According to pytest the tests pass.',
    'The test runner reports all tests pass.',
    'All tests pass on Python 3.12.',
    'The build is passing.',
]

# Fires although the claim is attributed to someone else. These fire on main
# too, so they are pre-existing limitations rather than anything this patch
# introduced — the distinction the review asked to keep visible.
ACCEPTED_LIMITATIONS = [
    'The contributor says the pipeline is complete.',
    'The contributor says the fix is complete and the migration is done.',
]


def _fires(text):
    return bool(find_claims(text))


def test_no_regression_against_main():
    """The property the whole narrowed patch rests on: anything main catches,
    this still catches. Asserted corpus-wide rather than case by case, because
    the failure it guards is a class, not an instance. Earlier drafts of this
    branch added exemptions to support an evidence-claim pattern; those
    exemptions repeatedly suppressed claims main caught. Removing them removed
    the class, and this test is what keeps it removed."""
    lost = [t for _, main_fires, _, t in CORPUS if main_fires and not _fires(t)]
    assert lost == [], f"regressions against main: {lost}"


def test_no_new_false_fires():
    """Nothing quiet on main may start firing unless it is a real claim."""
    new = [t for _, main_fires, want, t in CORPUS
           if not main_fires and not want and _fires(t)]
    assert new == [], f"new false fires: {new}"


def test_recorded_gains_still_fire():
    """The detections this patch exists to add, named individually so that
    losing one names itself."""
    lost = [t for t in GAINS if not _fires(t)]
    assert lost == [], f"gains lost: {lost}"


def test_deferred_cases_are_still_deferred():
    """Pinned so a future change that catches one is noticed rather than
    discovered. A failure here is good news and means: move the case out of
    DEFERRED and give it a real test."""
    for text in DEFERRED:
        assert not _fires(text), (
            f"{text!r} now fires - move it from DEFERRED into GAINS")


def test_accepted_limitations_are_not_regressions():
    """These fire and arguably should not. They fire on main too, so the patch
    neither caused them nor fixed them."""
    for text in ACCEPTED_LIMITATIONS:
        assert _fires(text), f"{text!r} stopped firing; re-check the corpus"
