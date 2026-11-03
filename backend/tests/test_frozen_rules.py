"""Prompt rules proven by Julian's grades, frozen (decisions/rule-evals-plan.md, table A).
Each sentence below is pinned word for word in the prompt it lives in, so changing one fails
here: a proven rule changes only when Julian says so, and then its line here changes with it.
Each case names the grade that proved the rule. Not frozen on purpose: the camera style of
the video prompt's opener, "One continuous shot" (#117) and the "while" sentence (round 2 N5
not graded yet)."""

import re
from pathlib import Path

import pytest

from agents.producer import INSTRUCTIONS as PRODUCER_INSTRUCTIONS
from gateway.catalog import MODEL_FOR_PURPOSE, SLOW_ANSWER_SECONDS
from jobs import clip_judge, owner_parts, watcher
from jobs.checks import FACT_CHECK_INSTRUCTIONS
from jobs.clip_judge import (
    FRAMES_PER_SECOND,
    MOTION_CHECK,
    PRODUCT_FRAMES_PER_SECOND,
    Check,
)
from jobs.photos import LABEL_INSTRUCTIONS, PICK_INSTRUCTIONS
from jobs.picture_check import (
    ASKS,
    FAILS_TO_FAIL,
    MOST_REDRAWS,
    PICTURES_SHOWN,
    STARTING_PICTURE_CHECK,
)
from jobs.planning import (
    BROLL_ONE_ACTION_INSTRUCTIONS,
    H3_PLAN_INSTRUCTIONS,
    H3_SCENE_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
)
from jobs.scenes import (
    BEFORE_PICTURE_INSTRUCTIONS,
    PERSON_IN_ROOM_PROMPT,
    ROOM_ALONE_PROMPT,
)

PLANNER = PLAN_INSTRUCTIONS


def test_h3_kind_choice_and_line_length_rules_stay_as_approved() -> None:
    assert H3_SCENE_INSTRUCTIONS == (
        "The first scene is always the person talking to camera. For each scene ask: can she show "
        "what this line says with the product in her hands or on herself, at arm's length? If yes, "
        'choose kind "talking": her face is in frame and she talks while showing the product, '
        "using it, or showing its result. Showing the product to the lens counts as its action; "
        'never a plain talking head. If no, choose kind "broll": hands and product only, her face '
        "out of frame, and her voice speaks off camera. Give kind_why: what her hands cannot show. "
        "For either kind, shows names its one action with the product, never null. The action and "
        "every claim must come from the page, the product photos or the shop owner's words. "
        "The first scene stays talking even when it can only show the product to the lens. "
        "With no target length, plan 3 or 4 scenes. With a target, use as many scenes as it needs. "
        "Write every line to fill 8 to 10 seconds at 2.5 words a second, about 20 to 25 words. "
        "Every line must take at least 4 and at most 14 seconds (10 to 35 words) at that speed. "
        "H3 makes the voice from the same voice description in every clip; no voice file is made "
        "in advance. Each action fills the clip and ends with an action after the last word. "
        "The one-action rule below applies to both kinds. Each scene has its own action.\n"
    )
    assert H3_SCENE_INSTRUCTIONS in H3_PLAN_INSTRUCTIONS
    assert BROLL_ONE_ACTION_INSTRUCTIONS in H3_PLAN_INSTRUCTIONS
    assert "Every scene between them is a B-roll scene" not in H3_PLAN_INSTRUCTIONS
    assert '"has face"' not in H3_PLAN_INSTRUCTIONS


FROZEN = [
    pytest.param(
        PLANNER,
        "Putting the product on or taking it off, attaching, fitting or adjusting it is a "
        "movement of its own: a scene films it or what comes after it, never both.",
        id="A2 two end states are two clips (#5 bag PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "When a claim needs two end states, give each its own B-roll scene, back to back, "
        "each with its own line and its one action, and mark the second as the second state.",
        id="A2 two end states are two scenes, the second marked (#5 bag PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "When the page or the line lists steps for using the product, film only the main step, "
        "the one that shows it working, such as the cloth wiping a stain away rather than "
        "spraying, waiting and rinsing, and write that B-roll line about that step.",
        id="A3 main step only (#8 toilet PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "A hand never turns, flips or spins the product, and it never spins by itself, to show "
        "it or another side of it:",
        id="A11 never turn or flip, planner (#12 V3 and round 1 #12 FAIL)",
    ),
    pytest.param(
        BEFORE_PICTURE_INSTRUCTIONS,
        "Every problem any scene shows or the product fixes, on those things, as it looks "
        "before: all of them, even those only one scene names,",
        id="A19 the before-picture holds every scene's problem (round 3 #8 s3 FAIL)",
    ),
    pytest.param(
        BEFORE_PICTURE_INSTRUCTIONS,
        "No product, no person and no hand: each scene's own picture adds them, the product "
        "from its shop photo and the presenter from the portrait.",
        id="A19 the before-picture has no product or person (before-picture test round 2 all 8 "
        "PASS)",
    ),
    pytest.param(
        PLANNER,
        "A claim about something the product, or a part of it, does that a camera could see "
        "is a B-roll scene that shows it.",
        id="A15 a visible claim is shown in B-roll (round 1 #8 s3 FAIL)",
    ),
    pytest.param(
        PLANNER,
        "The first scene is always the person talking to camera.",
        id="A16 first scene talking (round 1, all 5 ads)",
    ),
    pytest.param(
        PLANNER,
        "Give each B-roll scene a different action: never film the same action in two scenes.",
        id="#210-10 a different action per B-roll scene",
    ),
    pytest.param(
        PLANNER,
        "The video moments show how the product is really used: plan from them.",
        id="#210-10 plan from the moments",
    ),
    pytest.param(
        PLANNER,
        "Plan only actions the product really does: ones the page, the shop owner's words or a "
        "video moment shows it doing.",
        id="#210-10 only actions the product really does (requirement 9)",
    ),
]


@pytest.mark.parametrize(("prompt", "sentence"), FROZEN)
def test_a_proven_rule_is_still_in_its_prompt_word_for_word(prompt: str, sentence: str) -> None:
    assert sentence in prompt


# The planner's ask for something of the product no photo shows, for
# the owner part loop (Oct 19, spec handoffs/owner-part-approval): not proven by grades yet,
# pinned so it changes only when he says so.
OWNER_PART_ASK = [
    "For something of the product no photo shows, offer three answers: attach a photo of it; "
    "describe how it looks in one sentence; or have you draw it from your own idea of it.",
    "Say that they will see the scene's picture and say yes to it before its video is made.",
    "For something of the product no photo shows: if they attach a photo, plan with it among "
    "the scene's needs; if they describe it, give the scene an owner part from the shop "
    "owner's words, its looks their sentence as they said it; if they say to draw it, give "
    "the scene an owner part you invent, its looks one sentence of how such a thing "
    "ordinarily looks: the only thing you may make up.",
]


@pytest.mark.parametrize("sentence", OWNER_PART_ASK)
def test_the_owner_part_ask_is_in_the_planner_word_for_word(sentence: str) -> None:
    assert sentence in PLANNER


def test_the_owner_part_ask_names_no_product_and_no_example() -> None:
    # CODING_STANDARDS.md: a rule must hold for products never tested. A name is capitalised
    # and an example comes after "such as": the ask has neither past each sentence's start.
    for sentence in OWNER_PART_ASK:
        assert "such as" not in sentence
        assert not re.search(r"(?<![.:]) [A-Z]", sentence), sentence


def test_the_person_in_room_picture_is_ordered_to_show_the_presenter_standing_only() -> None:
    # Not graded yet, frozen as Julian ordered it (Oct 28 17:37, "just standing straight in
    # that room. that's it."; spec #210 point 12): an order, not a ban.
    assert (
        "The person stands straight and still in the room, hands at their sides, and nothing "
        "else happens."
    ) in PERSON_IN_ROOM_PROMPT


def test_the_room_alone_is_ordered_to_hold_only_the_room() -> None:
    # Not graded yet, frozen as Julian decided it (spec 2 item 53, Oct 28 22:32): "the
    # room-alone picture holds only the room, nothing of the scene".
    assert (
        "Keep the room itself as it is: its walls, floor, surfaces and fittings. Take out every "
        "other thing, and every mark, stain or mess on what stays, so nothing of any scene is "
        "left."
    ) in ROOM_ALONE_PROMPT


# A21: the start-picture judge, word for word as tested on 98 pictures Julian graded
# (PARTS_WITH_PHOTO in /mnt/project-files/evals/picture-judge/prompts.py): with the shop photo
# on gpt-6.1-sol, 3 asks failing on 2, it caught 6 of his 10 wrong pictures 5 runs of 5 and
# failed none of 52 good ones (Julian's yes 2026-10-10 23:05, decisions/built-picture-judge.md).
THE_JUDGE_AS_TESTED = """\
You check an AI-drawn photo before it is animated into a video ad. Picture models sometimes \
draw objects that cannot exist, such as a car with headlights on its back, a door hinged on \
both sides, or a hand with six fingers. Find those, and only those.

Work in two steps.
1. Look before you judge. For each main object in the picture (the products, devices, \
furniture and tools, and any hands holding them), write down which side of it faces the \
camera and every part you can see on that side, and where. Describe what is actually drawn, \
not what such an object usually has. Look closely at small parts: lenses, buttons, screens, \
spouts, caps, hinges, handles, fingers.
2. Then judge each object: could a real one of that object, seen from that side, show \
exactly those parts in exactly those places? Compare with how the real object is built: which \
parts sit on which side, and what you could and could not see from one viewpoint at once.

Decide "fail" only when you are sure something is physically impossible: a part on a side \
where the real object can't have it, two sides of one object seen at once, a part fused \
into another, a body part that can't be. Decide "pass" for everything else, including small \
differences in shape, size or proportion, odd but possible poses, blurry or unreadable \
print, dirt or mess, lighting, and anything merely unusual or unlikely.

You may also be shown one shop photo of the product. It tells you what the product is and \
how it is built: which parts it has and where they sit. Use it only for that. Ignore the \
photo's background, setting and anything being done with the product; only the product \
itself matters. Never compare the two pictures: differences in size, shape, angle, distance, \
colour, label or print are not a fail, and the product may be held, turned, upside down or \
partly hidden. Fail the product only if it shows a part a real one can't have there, two \
sides at once, or a label that didn't turn with it."""


def test_the_start_picture_judge_is_unchanged_from_the_one_tested() -> None:
    assert STARTING_PICTURE_CHECK == THE_JUDGE_AS_TESTED
    assert PICTURES_SHOWN == "The picture to judge is first; the shop photo, when shown, is second."


def test_the_start_picture_judge_runs_as_tested() -> None:
    assert MODEL_FOR_PURPOSE["check_starting_picture"] == "gpt-6.1-sol"
    assert (ASKS, FAILS_TO_FAIL) == (3, 2)
    # Julian 2026-10-10 21:48: two redraws, then the run stops.
    assert MOST_REDRAWS == 2


# A22: the B-roll clip judge, word for word as proved on the 45 graded clips (SIDES_PROOF and
# MOTION_PROOF in /mnt/project-files/evals/clip-judge-v2/prompts.py, the proof arms PS and PM):
# on gpt-6.1-sol, 3 asks failing on 2, it wrongly failed 2 of 29 good clips and caught 6 of 12
# faults, the bar met (decisions/julian-away-judgment-calls.md calls 10 and 12;
# decisions/built-clip-judge-in-app.md). The wording is Julian's to change.
# The phone-flip SIDES check went with the B-roll starting picture it judged a clip beside
# (spec #210 point 19, #241); MOTION's proved words stay the base of MOTION_FROM_REFERENCES.

CLIP_MOTION_AS_PROVED = """\
You check a short video clip for a product ad before it is shown to the shop owner. You decide \
whether everything in it moves and changes the way it would in real life, and whether it shows \
what the scene was meant to show.

You get frames from the clip, in order, each labelled with its time in seconds, so you can \
follow what moves between them. You also get the scene's order: one sentence saying what the \
clip should show.

Work in two steps.
1. Look before you judge. Fill in the look form from the frames: what is in the clip; how \
everything is at the first frame; then every movement and change of the product, of the hands \
touching it, and of the one thing the action changes (a surface, a liquid, a level, hair, a \
shape), step by step, with the time of the frame where you see it; and how everything is at the \
last frame. Describe what is on the frames, not what the product is for or what usually happens. \
Everything in the frame is your concern, not only the product: write down anything else that \
moves or changes too, and judge it in the questions as well. Anything that changes while nothing \
acts on it is a fault.
2. Then answer three questions, each "pass" or "fail".
- real_motion: does every object and liquid move the way a real one would? Use the facts about \
the product: a thick gel must move like a thick gel, a hard object must stay rigid.
- sudden_change: does the changed thing change only while something acts on it, and only where \
it acts, step by step? Fail when it changes before the action reaches it, where nothing touched \
it, or all at once between two frames; or when the product itself switches shape or state \
between frames.
- does_what_was_ordered: does the clip do the main action of the order with the product? Fail \
only when that main action is missing, a different main action is done with the product, or the \
person, hand or object the order names is not in the clip. A camera move, a close-up, the \
framing, the pace, or something the order says should be "in view" or "emphasized" is never a \
fail. Small extra movements around the main action, walking, posing or holding are not a fail.

Decide "fail" only when you are sure from the frames. When a question passes, its problem is \
null. When it fails, write up to 3 sentences: what went wrong and with which part, the second it \
starts, and what a real one would do.

You may also be shown one shop photo of the product, and facts about it from the shop. They tell \
you what the product is, what it is made of, what is inside and how it is built. Use them only \
for that. Never compare the frames with the photo: differences in size, angle, colour, label or \
print are not a fail."""


def test_the_clip_judge_is_unchanged_from_the_one_proved() -> None:
    assert MOTION_CHECK == CLIP_MOTION_AS_PROVED


# Changed on purpose for #210 point 18 (spec 2 items 27 and 28; ticket #228): a clip that
# carries its sent list is judged with the judge build brief's words (handoffs/judge-build/
# brief.md, section 3): PRODUCT word for word from the hand test's harness (run.py
# PRODUCT_CHECK, pinned in tests/fixtures/clip_judge_product_check.txt), MOTION as proved with
# only its shop-photo paragraph replaced (the brief's MOTION_NEW); and it is first asked
# whether the references and the behaviour note show enough to judge the clip.
CLIP_MOTION_NEW_PARAGRAPH = (
    "Before the clip you are shown what the video API that made the clip was given: the "
    "reference pictures, each labelled as in the prompt, frames from the reference video when "
    "there was one, and the prompt itself. They tell you what the product is, what it is made "
    "of, how it is built and how a real one moves and behaves. Use them to know how a real one "
    "moves."
)


def test_a_clip_with_its_sent_list_is_judged_with_the_judge_briefs_words() -> None:
    pinned = Path(__file__).parent / "fixtures" / "clip_judge_product_check.txt"
    assert clip_judge.PRODUCT_CHECK == pinned.read_text()
    proved_head = CLIP_MOTION_AS_PROVED[: CLIP_MOTION_AS_PROVED.index("You may also be shown")]
    assert clip_judge.MOTION_FROM_REFERENCES == proved_head + CLIP_MOTION_NEW_PARAGRAPH
    assert MODEL_FOR_PURPOSE["check_clip_product"] == "gpt-6.1-sol"


def test_the_judge_first_asks_whether_the_references_show_enough() -> None:
    # Spec 2 item 27 (Julian Oct 28 23:16): Claude's wording, the logic his.
    question = " ".join(clip_judge.REFERENCES_QUESTION.split())
    assert (
        "Your one question: do the references and the note show enough of the real product to "
        "judge what the clip does with it?"
    ) in question
    assert (
        '"product not shown" when the clip shows a side, a state or an action of the product'
    ) in question
    # Round 7 item 11 (Julian Oct 30 02:57): one detail not shown leaves the checks running.
    assert '"detail not shown" when the references and the note show the product' in question
    assert "You do not judge whether the clip is right." in question
    # Spec 2 item 28 (Julian Oct 28 22:38): every ask is told how this product behaves.
    assert clip_judge.SentClipCheckHandoff.model_fields["about_how_it_behaves"].default == (
        clip_judge.BEHAVIOUR_FRAME
    )


def test_the_clip_judge_asks_for_each_problem_in_up_to_3_sentences() -> None:
    # Decider call 2 (2026-10-13 05:33): the auto-redo's prompt writer needs what went wrong,
    # when, and what a real one would do; the proof ran with these words.
    assert Check.model_fields["problem"].description == (
        "When it fails: up to 3 sentences: what went wrong and with which part, the second it "
        "starts, and what a real one would do. Null when it passes."
    )


def test_the_clip_judge_runs_as_proved() -> None:
    # Decider call 10: the proof's wrong fails pick the model, sol for every check.
    for check in ("references", "product", "motion"):
        assert MODEL_FOR_PURPOSE[f"check_clip_{check}"] == "gpt-6.1-sol"
        # Each ask may take as long as the harness waited.
        assert SLOW_ANSWER_SECONDS[f"check_clip_{check}"] == 600
    # PRODUCT at 10 frames a second (SIDES' phone-flip rate, Julian's yes 2026-10-14 18:30; it
    # was 5 stills, call 9), MOTION at 5 frames a second, each asked once: the vote is gone
    # (Julian 2026-10-22 21:09, "drop the vote").
    assert (PRODUCT_FRAMES_PER_SECOND, FRAMES_PER_SECOND, clip_judge.ASKS) == (10, 5, 1)


# The photo picker's lines for hidden pictures, every record photo and video players, as the
# 126-page test proved them (runs/photo-video-pick-test/run.py, Julian's yes 2026-10-19 20:53).
# Gallery videos: every one in the gallery, picked by where it sits, not by its frame (Julian
# 2026-10-22 03:01, "it's just a prompt change").
# Official-photo line-ups: kept with the product named, not left out (requirement 25, Julian
# 2026-10-26 20:47, "we shouldn't drop this, instead tell which one is the product"; ruled for
# #212 on 2026-10-29).
PICKER_SHOWN_BESIDE_AS_TESTED = """\
Three more kinds of picture come with the screenshots:

- Hidden pictures. Some numbered pictures could not be drawn in the screenshots because they \
sit inside a scroll box or behind a carousel slide, so you only see their number in the lists. \
Those are given to you as their own small pictures, labelled "I<n> (hidden)". The hidden-picture \
list says which box each one sits in and which drawn pictures share that box: a hidden picture \
that shares the gallery's box with gallery pictures is usually another gallery photo. Judge them \
by the same rules and answer with their I numbers in gallery_images or more_images.

- The official photos R1, R2, ... are no longer a guide only: when an official photo shows \
this product and no picked I picture is the same photo, put its R number in record_images. \
Leave out official photos of other products or bundles by the same rules; keep a line-up with \
this product in it as above.

- Video players V1, V2, ... are the page's videos, each shown as its poster or first frame, \
with where it sits (box, position, and which pictures share its box). Answer gallery_videos \
with the V numbers of every video that sits in THIS product's own photo gallery: placed with \
the product photos, as one of the gallery's slides or thumbnails. Where a video sits decides, \
not what its frame shows: the frame only tells the players apart and is not used to judge what \
the video shows, which a later step does by watching it. So the rules above about other \
products and about leaving a picture out when unsure are for pictures only: never leave out a \
gallery video because its frame looks like another product, shade or version. Never a video \
in a reviews, customer, creator, "shop the look", social or shoppable-video section, and never \
a muted background banner. Say in video_notes why you left out each video you did not pick."""


def test_the_photo_picker_is_told_of_the_pictures_beside_the_screenshot_as_tested() -> None:
    assert PICK_INSTRUCTIONS.endswith("\n\n" + PICKER_SHOWN_BESIDE_AS_TESTED)


@pytest.mark.parametrize(
    "name",
    # The shops and platforms the picker was tested on (CODING_STANDARDS.md).
    ["Glow Recipe", "Rare Beauty", "OUAI", "Shopify", "Amazon", "Woo", "Wix", "Squarespace"],
)
def test_the_photo_pickers_rules_name_no_shop(name: str) -> None:
    assert name.casefold() not in PICK_INSTRUCTIONS.casefold()


# The watcher writes each moment as one action within the video API's longest piece (spec
# #210 point 4, user story 30: "As the watcher, I want to know the API's 15 s limit").
WATCHER_LONGEST_MOMENT = (
    "A moment lasts at most 15 seconds, the longest piece of video the video model takes: "
    "split a longer action at a natural point, such as where one step ends and the next begins."
)


def test_the_watcher_is_told_the_video_apis_15_second_limit() -> None:
    assert WATCHER_LONGEST_MOMENT in watcher.INSTRUCTIONS
    assert MODEL_FOR_PURPOSE["watch_video"] == "gemini-3.8-flash"


# What the planner and the fact check are told of the product's videos, each line Julian's
# "keep" (2026-10-19 22:35, decisions/video-context.md).
PLANNER_VIDEO_PICTURE = (
    "A picture marked as from the product's video is a product photo like every other, "
    "showing the product's looks at that moment; the line beside it says what it shows that "
    "no photo shows."
)
PLANNER_VIDEO_WORDS = (
    "What the product's video says is the shop's own words, used like the page text; "
    '"music only" means it says nothing.'
)
FACT_CHECK_VIDEO_WORDS = (
    "What the product's video says counts as the shop's own words, like the page's text."
)
VIDEO_LINES = [
    pytest.param(PLANNER, PLANNER_VIDEO_PICTURE, id="planner: a video picture is a product photo"),
    pytest.param(PLANNER, PLANNER_VIDEO_WORDS, id="planner: what the video says is the shop's"),
    pytest.param(
        FACT_CHECK_INSTRUCTIONS, FACT_CHECK_VIDEO_WORDS, id="fact check: the video's words count"
    ),
]


@pytest.mark.parametrize(("prompt", "line"), VIDEO_LINES)
def test_the_product_videos_lines_are_as_approved(prompt: str, line: str) -> None:
    assert " ".join(line.split()) in " ".join(prompt.split())


@pytest.mark.parametrize(
    "name",
    # The shops and products the video reader was tried on (runs/video-frames-prototype/reader/,
    # CODING_STANDARDS.md).
    [
        "Rare Beauty",
        "blush",
        "applicator",
        "AirPods",
        "Helinox",
        "Levi",
        "Greenies",
        "Lemi",
        "Loop",
    ],
)
def test_the_product_videos_lines_name_no_shop_or_product(name: str) -> None:
    for line in (
        watcher.INSTRUCTIONS,
        watcher.PROBE_INSTRUCTIONS,
        PLANNER_VIDEO_PICTURE,
        PLANNER_VIDEO_WORDS,
        FACT_CHECK_VIDEO_WORDS,
    ):
        assert name.casefold() not in line.casefold()


# The owner part (owner-part approval spec, Julian's design 2026-10-19): drawn from the shop
# owner's one sentence, or the planner's, exactly as it says. Pinned so a change is seen; not
# yet proved by a grade (the real picture test, T5, comes first).
OWNER_PART_RULES = [owner_parts.CHECK_RULE]
# Products and parts of the test ads, and the parts the rules were designed around: none may
# be named in a rule every product gets (CODING_STANDARDS.md, no hardcoding).
TESTED_PRODUCT_WORDS = [
    "glow recipe",
    "rare beauty",
    "olipop",
    "gaiam",
    "native pet",
    "pumpkin",
    "cocolab",
    "cocofloss",
    "floss",
    "blueland",
    "loop",
    "dr squatch",
    "rothy",
    "blush",
    "dew drops",
    "earplug",
    "soap",
    "applicator",
    "doe-foot",
    "wand",
    "brush",
    "tube",
    "gel",
    "lid",
    "cap",
    "fishwife",
    "salmon",
    "ember",
    "hexclad",
    "pan",
    "goodr",
    "sunglasses",
    "true classic",
    "polo",
    "mug",
    "toilet",
]


def test_the_owner_part_rules_are_unchanged() -> None:
    # The B-roll picture writer's rule and label for the part went with the B-roll starting
    # picture (spec #210 point 19, #241); the check's rule stays for the pictures still checked.
    (check,) = OWNER_PART_RULES
    assert check == (
        'One part of the product, named in "owner_part", is in no shop photo: it was drawn '
        "from the one sentence given there, which says how it looks. Use that sentence as you "
        "use the shop photo: only to know what that part is and how it is built. Fail that "
        "part only if it shows something a real one, built as the sentence says, can't have "
        "there."
    )


@pytest.mark.parametrize("rule", OWNER_PART_RULES)
def test_the_owner_part_rules_name_no_tested_product(rule: str) -> None:
    words = set(re.findall(r"[a-z-]+", rule.casefold()))
    named = [
        word
        for word in TESTED_PRODUCT_WORDS
        if (word in rule.casefold() if " " in word else word in words)
    ]
    assert named == []


# Julian, Oct 29 2026 19:12: every gpt-5.6-sol job moves to gpt-6.1-sol, with no comparison
# test.
MOVED_TO_6_1_SOL = (
    "produce",
    "look",
    "pick_photos",
    "note_face",
    "label_photo",
    "plan_ad",
    "rewrite_line",
    "shorten_script",
    "shorten_line",
    "lengthen_line",
    "choose_starting_picture",
    "plan_before_picture",
)


@pytest.mark.parametrize("purpose", MOVED_TO_6_1_SOL)
def test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol(purpose: str) -> None:
    assert MODEL_FOR_PURPOSE[purpose] == "gpt-6.1-sol"


def test_no_job_runs_on_gpt_5_6_sol() -> None:
    assert "gpt-5.6-sol" not in MODEL_FOR_PURPOSE.values()


# The first-send prompt (#210 point 16, decided; Julian's words in the decision record). It
# retires the frozen opener's "The camera stays still." and "A n-second video at real-time
# speed." (A1, now the speed line), the same-side sentence (A18) and reference mode's "Image 1
# is how the clip starts": their tests went with the old writer when the clip step switched to
# cut-and-send (#240).


# The old opener's side sentence (A18), retired by #210 point 16.
SAME_SIDE_RETIRED = "Whatever side faces the camera at the start stays toward the camera"


# The first message's path (spec #247 point 3, Julian Oct 29 03:33, Q8): the producer goes the
# usual order (updated by spec #303) unless the shop owner's words redirect it, and asks only
# when the ad can't go on without them. Its instructions, pinned word for word.
FIRST_MESSAGE_PATH = [
    pytest.param(
        "The usual order is: read the page, plan the ad, create the person, run the planning "
        "checks, create the music, and call start_scene_agent for every scene once its "
        "presenter and checked line exist. Start all ready scenes together in one turn, "
        "without waiting for one to finish before starting another. Give each scene your own "
        "instruction and exact chunks of the owner's messages in quotes. Code checks those "
        "quotes; if refused, quote their words exactly. The scene agent draws its starting "
        "picture and films, checks and fixes in its same chat. Code draws the shared room and "
        "presenter pictures before filming. The starting picture needs no owner's OK, except "
        "the existing owner-part approval gate. Once every scene is finished, assemble the ad.",
        id="the usual order ends with the ad shown",
    ),
    pytest.param(
        "Ask only when the ad can't go on without them: their yes to the picture of a part no "
        "photo shows, something the ad needs that neither the page nor the library holds, or "
        "what a tool handed back for them to answer. Everything else you answer from what you "
        "already have, and go on.",
        id="ask only when the ad can't go on",
    ),
]


@pytest.mark.parametrize("sentence", FIRST_MESSAGE_PATH)
def test_the_producers_first_message_path_is_in_its_instructions_word_for_word(
    sentence: str,
) -> None:
    assert sentence in " ".join(PRODUCER_INSTRUCTIONS.split())


def test_nothing_in_the_producers_instructions_waits_for_a_yes_before_making_the_ad() -> None:
    # The old path showed a plan or the script and waited for the shop owner's yes.
    said = " ".join(PRODUCER_INSTRUCTIONS.split()).lower()
    for waiting in ("shall i", "wait for their yes", "wait for a yes", "plan card", "yes button"):
        assert waiting not in said


def test_everything_the_clip_judge_is_told_is_the_version_its_rulings_name() -> None:
    # Each ruling names the version of all the judge was told (clip_judge.wording_version):
    # its questions, the behaviour note's frame, labels, frame rates, handoffs and answer forms
    # with every field's words. They are Julian's to change (project rule, 2026-10-12): a
    # change to any of them needs his yes and a new version pinned here. Round 7 item 11
    # (Julian Oct 30 02:57): the first question's two "not shown" verdicts, DETAIL_LEFT_OUT.
    assert clip_judge.wording_version() == "664e081317"
    assert clip_judge.wording_version(fix=True) == "675696d4d3"
    # Spec #303 changes the inputs, while the questions and old-clip wording stay put.
    assert clip_judge.wording_version(scene=True) == "3c870a5e5b"
    assert clip_judge.wording_version(scene=True, fix=True) == "f38538a8b1"
    # Spec #327: talking clips expect a moving mouth, without changing the judge's questions.
    assert clip_judge.TALKING_SCENE_CONTEXT == (
        "The woman talks on camera in this clip; her mouth moving is expected."
    )
    assert clip_judge.wording_version(scene=True, talking=True) == "7da25e8420"
    assert clip_judge.wording_version(scene=True, talking=True, fix=True) == "2adb9ee1d3"


# A table, chart or diagram is a chart, and a line-up shows this product itself as a picture
# (#273 point 9, Julian 2026-10-29 21:53, "yes. i agree with your pick"): a line-up is cut out
# and a chart never is, so a price table labelled a line-up had parts invented.
CHART_KIND = (
    "chart (a chart, table or diagram, even one that compares versions, sizes or bundles of "
    "this product)"
)
LINE_UP_KIND = "line-up (this product itself, shown as a picture, beside other products)"


@pytest.mark.parametrize(
    "words",
    [pytest.param(PICK_INSTRUCTIONS, id="picker"), pytest.param(LABEL_INSTRUCTIONS, id="label")],
)
def test_a_table_is_a_chart_and_a_line_up_shows_the_product_itself(words: str) -> None:
    assert CHART_KIND in words
    assert LINE_UP_KIND in words
    assert "comparison of versions" not in words


# Spec #303 keeps the proven rules in the live scene agent, after retiring the old writer.
SCENE_PROVEN_RULES = [
    "B-roll must sell: every clip DOES something. Holding or placing the product fails, "
    "unless holding is the claim.",
    "Finishing steps such as putting it away or wiping up get no clip.",
    "One action per clip, except movements the owner explicitly asks for in their order; "
    "every redo keeps every step they asked for.",
    "A two-state claim needs two one-action clips joined by a cut.",
    "Always draw a starting picture: the starting picture is an order the clip must follow.",
    "Photos are for looks only; copying their pose is not their job.",
    "Talking clips ignore motion text: a talking-scene action needs a new starting picture "
    "or a B-roll scene.",
    "Use real-time speed.",
    "Never show a side the photos don't show.",
    "A result shows only where the product touches.",
    "In a problem scene nobody touches the product.",
]


@pytest.mark.parametrize("sentence", SCENE_PROVEN_RULES)
def test_the_live_scene_agent_keeps_the_other_proven_rules_word_for_word(sentence: str) -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    assert sentence in " ".join(INSTRUCTIONS.split())


H3_SCENE_RULES = [
    "A talking scene has her face in frame and shows the product in her hands, on her body, "
    "or its result while she says the line on camera.",
    "B-roll shows hands and product only, her face out of frame, with her voice off camera.",
    "Showing the product to the lens counts as the action in a talking scene.",
    "For a failing talking clip, first fix the cause the judge names; if the movement keeps "
    "failing, use a simpler action that still proves the line; last, show the product to "
    "the lens while she says the line.",
]


@pytest.mark.parametrize("sentence", [*H3_SCENE_RULES, SCENE_PROVEN_RULES[0]])
def test_h3_scene_agents_keep_the_approved_kind_and_fallback_rules(sentence: str) -> None:
    from agents.scene_agent.instructions import H3_INSTRUCTIONS

    assert sentence in " ".join(H3_INSTRUCTIONS.split())


def test_the_broll_picture_check_keeps_its_two_approved_questions_word_for_word() -> None:
    from agents.scene_agent.picture_check import CHECK_INSTRUCTIONS

    words = " ".join(CHECK_INSTRUCTIONS.split())
    assert (
        "1. Does the product, with what is inside it, look the same as in its photos: its shape, "
        "colours, parts, printed words and contents? Where the photos don't show a part, the "
        "page's words say how it looks. A part the photos never show, drawn plausibly, "
        "doesn't fail it."
    ) in words
    assert (
        "2. Does anything in the picture break physics: something no real camera could film, "
        "such as a person brushing hair with no head under it, a hand that isn't joined to an arm, "
        "an object floating, or a thing passing through another? A slightly wrong hand "
        "doesn't fail it."
    ) in words


def test_the_live_planner_and_scene_agent_allow_camera_movement_beside_the_action() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    for words in (PLAN_INSTRUCTIONS, INSTRUCTIONS):
        assert "The camera never moves" not in words
        assert "never move the camera" not in words
    assert "A camera movement may accompany that action; it does not replace it." in " ".join(
        PLAN_INSTRUCTIONS.split()
    )
