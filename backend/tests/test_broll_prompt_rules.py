"""What the B-roll prompt and picture models are told, by kind of scene. Plain checks of the
prompt text, kept apart from tests/test_producer_broll.py so they run without a database."""

import pytest

from jobs.first_send import SPEED_LINE
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    broll_prompt_instructions,
)

from .test_frozen_rules import BROLL_PROMPT as FROZEN_BROLL_PROMPT
from .test_frozen_rules import FROZEN

# The frozen sentences of the B-roll prompt (test_frozen_rules.py) are shared rules, so each
# is told for every kind, not only the "does a job" kind that file reads.
FROZEN_SHARED_RULES = [
    pytest.param(str(sentence), id=frozen.id)
    for frozen in FROZEN
    for prompt, sentence in [frozen.values]
    if prompt == FROZEN_BROLL_PROMPT
]


@pytest.mark.parametrize(
    "rule",
    [
        "One continuous shot",
        # Since 09 Oct "shows", not the line: a line that can't be filmed is said over
        # another B-roll, never acted out (test ads, 08 Oct; Julian 23:40).
        'The product, or the tool used with it, does what "shows" describes',
        'the way "usage" says',
        "in the middle of the frame",
        "Say nothing about the top or the bottom of the frame",
        "Only the presenter is shown",
        "Any hand is fine",
        "The product is shown, not described",
        'Never write "no speech", "no sound" or "no text"',
        "only the product",
        "the setting is described in words",
        'the "before"',
    ],
)
def test_the_model_planning_the_picture_is_given_the_shared_prompt_rules(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


# Rules Julian's grades proved
# (decisions/agreed-vs-built-2026-10-08.md rows 1, 5, 7, 10, 17). The sentences frozen word
# for word (no timings, one movement where the product does the work, a person named in
# "shows" is in the clip, never the camera, never turn or flip, never the fiddly change, the
# product may stand, no contradictions) are pinned in test_frozen_rules.py; here are the rest.
@pytest.mark.parametrize(
    "rule",
    [
        # Only the scrub, with the voice carrying squeeze and flush, was PERFECT (#8 5 s);
        # "flows as one" let clamp + rotate + glide through and Boreal skipped the middle
        # (N5 s2), so Julian made it one movement, no exceptions.
        "Film one movement only",
        '"usage" tells how it is held and used, not how many steps to film',
        # A plan "shows" of holding up the phone after its drop was overridden back to the
        # drop by the line's claim, 4 runs of 4 (#12, compare.md cause check), so "shows"
        # decides which movement.
        '"shows" says which: when it names one movement, film that one, even when the line',
        # Test ads (08 Oct), N5 Le Duo: the turn was moved before the glide, so the clip filmed
        # the straightening how-to under a line about curls.
        'A part that "shows" or "usage" says happens while the movement is done, such as '
        "pressing down while turning a cap, is part of that one movement: film it during the "
        "movement, never before it or in the starting picture.",
        # Code puts only the speed line first, the same one every clip is sent (spec #210
        # point 16; #242 review S1: Claude's wording, not approved yet).
        'Code starts the video prompt with the speed line, "Film this in real-time speed.", so '
        "never write the speed, and never move the camera or say where or how close it ends.",
    ],
)
def test_the_model_writing_the_prompts_is_told_the_proven_rules(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


@pytest.mark.parametrize(
    "dropped",
    [
        "One action every 2 to 3 seconds",
        "not standing idle beside the action",
        # The API already sends the shape; the video prompt doesn't repeat it.
        "Upright 9:16",
        "flow as one",
        # Test ads (08 Oct): every B-roll looked phone-filmed; Julian: "there is no extra
        # benefit". A handheld look also moves the camera, against "never the camera".
        "phone",
        "casual",
        "never call the camera fixed",
        # A labelled scene's prompts are no longer planned from "shows" alone: the rules say
        # what is made up and what isn't. The unlabelled instructions keep the sentence.
        "Make nothing up",
    ],
)
def test_the_model_writing_the_prompts_is_no_longer_told_the_rules_grades_rejected(
    dropped: str,
) -> None:
    # Absent from the whole text the model is told, whichever kind the scene is: a rejected
    # rule can't come back through a kind's own rules.
    for kind in BROLL_KIND_RULES:
        assert dropped not in broll_prompt_instructions(kind), kind


@pytest.mark.parametrize("sentence", FROZEN_SHARED_RULES)
@pytest.mark.parametrize("kind", BROLL_KIND_RULES)
def test_every_kind_of_b_roll_scene_is_told_each_frozen_shared_rule(
    kind: str, sentence: str
) -> None:
    assert sentence in broll_prompt_instructions(kind)


# What both best start pictures did (#8 toilet 5 s, #12 V2): rows 9, 18, 31. The sentence
# saying what must be right (camera, parts, counts, hands) is pinned in test_frozen_rules.py.
@pytest.mark.parametrize(
    "rule",
    [
        'The picture prompt opens: "An upright 9:16 photo in a real, ordinary <place>."',
        "say the pose once and what it does to the product's shape",
    ],
)
def test_the_picture_prompt_shows_what_must_be_right(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


def test_a_scene_showing_the_problem_shows_it_plainly_and_never_changes_it() -> None:
    # Test ads (08 Oct), N3: the shower's "builds up" line was talking; Julian: "As a B-roll
    # scene." Clips are real-time, so the dirt is shown as it is, not building up.
    rules = BROLL_KIND_RULES["shows the problem"]
    assert "plainly, in the starting picture, with the product in view" in rules
    assert "Nothing about the problem changes in the clip." in rules
    # Round 2 (Julian 21:22 "yes"): the example was a filler action.
    assert "set down beside it" not in rules


def test_a_scene_showing_the_problem_has_nobody_touching_the_product() -> None:
    # Round 3 #8 s3 (Julian 2026-10-10 05:30, FAIL): "the one movement is a hand or the
    # product" put a hand on the bottle with nothing real to do, and the prompt asked the
    # lid contact to ripple the bowl water, so the video model invented a pour ("It looks
    # like water ... the lip is not being opened"). s2 (PASS) had nobody touching the bottle
    # and only the water moving.
    rules = BROLL_KIND_RULES["shows the problem"]
    assert (
        "Nobody touches the product: no hand holds it in the starting picture or the clip, "
        "and it stands still, label to the camera, the whole time."
    ) in rules
    assert (
        "The one movement is something natural already in the scene, such as water rippling, "
        "steam rising or a drip falling, and it never comes from the product or does anything "
        "to the problem."
    ) in rules
    assert "The one movement is a hand or the product" not in rules


def test_a_does_a_job_scene_ends_on_its_result() -> None:
    assert "ends on the result" in BROLL_KIND_RULES["does a job"]


def test_a_does_a_job_scene_films_its_result_happening() -> None:
    # Round 2 #8 s4: the bottle beside a clean toilet. Julian: "there is no evidence that the
    # toilet is so clean right now ... we have to show."
    assert (
        "A result is filmed happening, from before to after in the same clip. A clean or "
        "finished thing with the product beside it, with no change filmed, never shows a "
        "result."
    ) in BROLL_KIND_RULES["does a job"]


def test_a_drop_starts_at_the_height_the_page_states() -> None:
    # Round 2 #12 s5: dropped from just above the floor; Julian: "dropping at a really close
    # position from the floor". The shop's photo says "6 ft. drop tested". No hardcoding:
    # with no height stated, a standing person's hand.
    assert (
        "When the action is a drop, fall or throw, the starting picture shows the height it "
        "starts from: the height the page or its photos state, such as 6 ft above the floor; "
        "when none is stated, held in a standing person's hand."
    ) in BROLL_SHARED_RULES
    # Round 2 #12 s5's own picture prompt put "the camera at knee height", so the hand was
    # drawn just above the floor; 15 redraws without that never copied the low drop photo.
    assert (
        "The camera is placed to see the whole fall, from that height down to where it lands: "
        "never at knee height or close to the floor."
    ) in BROLL_SHARED_RULES


def test_a_does_a_job_scene_never_films_putting_the_product_on() -> None:
    # The toilet clip's whole ring vanished where nothing touched it, "time-compressed", after
    # filming the gel go on; Julian: "just scrub it and then that scrub area become clean",
    # "you don't need to even put in the gel at all"
    # (decisions/2026-10-08-result-only-where-scrubbed.md). The result-only-where-touched
    # sentence is pinned in test_frozen_rules.py.
    assert "putting the product on is never filmed or drawn" in BROLL_KIND_RULES["does a job"]


def test_a_showcase_scene_ends_on_what_it_shows() -> None:
    showcase = BROLL_KIND_RULES["showcase"]
    assert 'ends on the moment that shows what "shows" describes, done by a hand' in showcase
    # Round 2: "picked up or set down" made filler B-roll, and every one failed (#12 s2, #8
    # s4, N3). The clutch carry was graded PERFECT (#5), so wearing it as claimed stays.
    assert "picked up or set down" not in showcase
    assert (
        "This scene proves its line by the product being used or worn the way the line "
        "claims, such as a bag carried as a clutch while walking. A hand only holding, "
        "placing, setting down, standing up or pointing at the product never proves anything: "
        "never film that."
    ) in showcase
    # The forced ending made pointless zooms (#15, #4): "a lot better" without it.
    assert "at its best" not in showcase
    assert "camera move" not in showcase


def test_the_speed_line_the_writer_is_told_of_is_the_one_code_sends() -> None:
    # A clip made from what it is sent starts with the speed line only (spec #210 point 16):
    # no length, no still-camera line (#242 review S1).
    assert f'"{SPEED_LINE}"' in BROLL_SHARED_RULES
    assert "clip's length" not in BROLL_SHARED_RULES
