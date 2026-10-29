"""Prompt rules proven by Julian's grades, frozen (decisions/rule-evals-plan.md, table A).
Each sentence below is pinned word for word in the prompt it lives in, so changing one fails
here: a proven rule changes only when Julian says so, and then its line here changes with it.
Each case names the grade that proved the rule. Not frozen on purpose: the camera style of
the video prompt's opener, "One continuous shot" (#117) and the "while" sentence (round 2 N5
not graded yet)."""

import re
from pathlib import Path

import pytest

from agents import fixing_agent
from gateway.catalog import MODEL_FOR_PURPOSE, SLOW_ANSWER_SECONDS
from jobs import clip_judge, owner_parts, watcher
from jobs.checks import FACT_CHECK_INSTRUCTIONS
from jobs.clip_judge import (
    FRAMES_PER_SECOND,
    MOTION_CHECK,
    SIDES_CHECK,
    SIDES_FRAMES_PER_SECOND,
    Check,
    SidesVerdict,
)
from jobs.first_send import FIRST_SEND_INSTRUCTIONS, PIECE_LABEL, SPEED_LINE
from jobs.photos import PICK_INSTRUCTIONS
from jobs.picture_check import (
    ASKS,
    FAILS_TO_FAIL,
    MOST_REDRAWS,
    PICTURES_SHOWN,
    STARTING_PICTURE_CHECK,
)
from jobs.planning import PLAN_INSTRUCTIONS
from jobs.scenes import (
    BEFORE_PICTURE_INSTRUCTIONS,
    BEFORE_PICTURE_JOB,
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    MAIN_PHOTO_JOB,
    SAME_SIDE,
    broll_prompt_instructions,
    broll_video_prompt,
)

PLANNER = PLAN_INSTRUCTIONS
# Every kind gets the same shared rules; "does a job" is one of them.
BROLL_PROMPT = broll_prompt_instructions("does a job")

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
        BROLL_PROMPT,
        "Never film the fiddly change between two states, such as clipping, unclipping or "
        "folding: open with it done.",
        id="A2 the change between two states is never filmed (#5 bag PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "When the page or the line lists steps for using the product, film only the main step, "
        "the one that shows it working, such as the cloth wiping a stain away rather than "
        "spraying, waiting and rinsing, and write that B-roll line about that step.",
        id="A3 main step only (#8 toilet PERFECT)",
    ),
    pytest.param(
        BROLL_PROMPT,
        'When "shows" lists more than one, film the one where the product, or the tool used '
        "with it, does the work, not a step before it, such as putting the product on, or "
        "after it, such as rinsing. The voice carries the rest.",
        id="A3 the voice carries the rest (#8 toilet PERFECT)",
    ),
    pytest.param(
        BROLL_PROMPT,
        'Never write seconds or timings, or words that slow it down, such as "slowly" or "gently".',
        id="A4 no timings (locked list)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "never move the camera or say where or how close it ends.",
        id="A4 no forced end zoom (locked list)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Never ask for two things that can't both be true at once, of the product or the scene, "
        'such as "upright" and "nozzle pointing down", or "squeeze" and "no gel". Check every '
        "order against the product photos and against your other orders.",
        id="A4 no contradictions (locked list)",
    ),
    pytest.param(
        BROLL_KIND_RULES["does a job"],
        "The result shows only where the product, or the tool used with it, touches, and "
        "nothing else changes. It happens as the touch passes, at a real-time pace: never all "
        "at once, sped up or time-compressed.",
        id="A5 result only where touched, nothing instant (toilet, Oct 8)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Anything that must be right goes in the starting picture, said plainly: say where the "
        "camera is, its height and angle; each object the action happens to, other than the "
        "product, with every part named as a real, ordinary one, like one you'd buy in any "
        "shop; the problem the product fixes, as it looks before; exact counts, and left or "
        "right; and any hand already in place for the action, holding what it uses.",
        id="A8 what must be right is in the start picture (#12 V1 FAIL, V2 GOOD)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "The product may stand in view, label to the camera, when holding it would bend its "
        "shape, while the tool used with it does the work.",
        id="A9 product may stand while a tool works (#8 5s scrub PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "A hand never turns, flips or spins the product, and it never spins by itself, to show "
        "it or another side of it:",
        id="A11 never turn or flip, planner (#12 V3 and round 1 #12 FAIL)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "The side of the product facing the camera in the starting picture faces it at the "
        "end: never turn, flip or spin the product, and after a drop it lands that side up.",
        id="A11 never turn or flip, video prompt (#12 V3 and round 1 #12 FAIL)",
    ),
    pytest.param(
        PLANNER,
        "The one verb is what a hand or the product does, never a camera move, such as the "
        "camera pushing in.",
        id="A14 a hand or the product moves, never the camera (round 1 #8 s2 FAIL)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Draw this scene in that place: the same light, and each thing in it with every "
        'stain, mark and part exactly as it is, even what "shows" doesn\'t name.',
        id="A19 every scene is drawn in the before-picture's place (round 3 #8 s3 FAIL, "
        "before-picture test round 2 all 8 PASS)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Put the camera where this scene's action is seen clearly, as close or as far as the "
        "action needs, in the same place: a hand at a table is seen from beside the table, not "
        "from across the room.",
        id="A20 the camera is as close as the action needs (before-picture test round 1 #5 s4 "
        "FAIL, round 2 all 8 PASS)",
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
        BROLL_PROMPT,
        'When "shows" names a person or a hand, they are in the clip, doing what it says.',
        id="A17 a person named in shows is in the clip (#5 s3 FAIL)",
    ),
]


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


@pytest.mark.parametrize(("prompt", "sentence"), FROZEN)
def test_a_proven_rule_is_still_in_its_prompt_word_for_word(prompt: str, sentence: str) -> None:
    assert sentence in prompt


def test_every_kind_of_b_roll_prompt_gets_the_frozen_shared_rules() -> None:
    shared = "never move the camera or say where or how close it ends."
    assert all(shared in broll_prompt_instructions(kind) for kind in BROLL_KIND_RULES)


def test_the_video_prompt_asks_for_real_time_speed() -> None:
    # A1: in the old opener that passed #12 and #8 and in today's. Only "real-time speed" is
    # frozen: the camera style that follows is open until a test compares.
    assert "at real-time speed." in broll_video_prompt("A hand pours the sauce.", 5)


def test_the_main_photo_is_sent_for_its_looks_only() -> None:
    # A6: Oct 7 #12 drop leak: a photo's pose is copied whatever its job says, so the main
    # photo's job is only how the product looks.
    assert MAIN_PHOTO_JOB == "the product, only how it looks"


def test_the_video_prompt_keeps_the_side_facing_the_camera() -> None:
    # A18: round 3 #12 s2 and s3 FAIL, the phone flipped mid-clip; with this sentence in
    # every video prompt, 9 clips Julian saw were all fine (decisions/built-phone-flip-fix.md).
    assert (
        "The camera stays still. Whatever side faces the camera at the start stays toward the "
        "camera the whole time. A hand pours the sauce."
    ) in broll_video_prompt("A hand pours the sauce.", 5)


def test_every_scene_picture_is_sent_the_before_picture_for_the_place_only() -> None:
    # A19: round 3 #8 s3 FAIL, "a different toilet means the different dirt"; drawn from one
    # before-picture, all 8 ads' scenes matched (Julian 2026-10-11 00:53, "all good. all pass").
    assert BEFORE_PICTURE_JOB == (
        "the place, its light and each thing in it, with every mark, exactly as they are"
    )


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
# The phone-flip SIDES check (harness arm F1, Julian's yes to ship 2026-10-14 18:30), pinned
# word for word beside its answer form in the clip judge's own tests.
CLIP_SIDES_AS_PROVED = (
    Path(__file__).parent / "fixtures" / "clip_judge_sides_check.txt"
).read_text()

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
    assert SIDES_CHECK == CLIP_SIDES_AS_PROVED
    assert MOTION_CHECK == CLIP_MOTION_AS_PROVED


def test_the_clip_judge_asks_for_each_problem_in_up_to_3_sentences() -> None:
    # Decider call 2 (2026-10-13 05:33): the auto-redo's prompt writer needs what went wrong,
    # when, and what a real one would do; the proof ran with these words.
    assert SidesVerdict.model_fields["problem"].description == (
        "When it fails: up to 3 sentences: the object and what is impossible about it, the "
        "frame time, and what a real one would look like. Null when it passes."
    )
    assert Check.model_fields["problem"].description == (
        "When it fails: up to 3 sentences: what went wrong and with which part, the second it "
        "starts, and what a real one would do. Null when it passes."
    )


def test_the_clip_judge_runs_as_proved() -> None:
    # Decider call 10: the proof's wrong fails pick the model, sol for both checks.
    assert MODEL_FOR_PURPOSE["check_clip_sides"] == "gpt-6.1-sol"
    assert MODEL_FOR_PURPOSE["check_clip_motion"] == "gpt-6.1-sol"
    # Each ask may take as long as the harness waited.
    assert SLOW_ANSWER_SECONDS["check_clip_sides"] == 600
    assert SLOW_ANSWER_SECONDS["check_clip_motion"] == 600
    # SIDES at 10 frames a second (the phone-flip check, Julian's yes 2026-10-14 18:30; it was
    # 5 stills, call 9), MOTION at 5 frames a second, each asked once: the vote is gone
    # (Julian 2026-10-22 21:09, "drop the vote").
    assert (SIDES_FRAMES_PER_SECOND, FRAMES_PER_SECOND, clip_judge.ASKS) == (10, 5, 1)


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
OWNER_PART = owner_parts.OwnerPart(what="PART", source="owner's words", looks="LOOKS")
OWNER_PART_RULES = [
    owner_parts.writer_rule(OWNER_PART),
    owner_parts.writer_rule(OWNER_PART.model_copy(update={"source": "invented"})),
    owner_parts.picture_label(OWNER_PART),
    owner_parts.CHECK_RULE,
]
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
    writer, invented, label, check = OWNER_PART_RULES
    assert writer == (
        'One part of the product this scene shows, "PART", is in none of the pictures sent: '
        "no photo shows it. How it looks comes only from the shop owner's own words: "
        '"LOOKS" Draw it exactly as that sentence says, nothing more: write every look the '
        "sentence gives it into the picture prompt, and never give it a colour, shape, size, "
        "material, mark or detail the sentence doesn't, or take its look from another part "
        "of the product. Code puts the sentence before your picture prompt too."
    )
    assert (
        "comes only from a sentence written for the shop owner, who sees the picture and "
        'approves it: "LOOKS"'
    ) in invented
    assert label == (
        "The PART is in none of the pictures: draw it exactly as this says, and add nothing "
        'to it: "LOOKS"'
    )
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


# The fixing agent's own instructions: the prototype's, with changes A1-A8 as Julian approved
# them (2026-10-19 16:55, "I'll go with your recommendations for question two and question
# four"; handoffs/fixing-agent/spec.md, section 3), its give-up tool named give_up and "That
# is an honest result, better than a bad clip." dropped once a give-up keeps its last try
# (Julian, 2026-10-23 19:35, "go with your picks"). Pinned word for word: any change fails
# here until he says so and the pinned copy changes with it.
FIXING_AGENT_AS_APPROVED = (
    Path(__file__).parent / "fixtures" / "fixing_agent_instructions.txt"
).read_text()


def test_the_fixing_agents_instructions_are_the_approved_ones_word_for_word() -> None:
    assert fixing_agent.INSTRUCTIONS == FIXING_AGENT_AS_APPROVED


# What giving up does, as the agent is told it at each door, pinned word for word (Julian's
# OK 2026-10-19 20:10; door 1's since 2026-10-23 19:35): door 1's scene keeps its last try,
# door 2's keeps its clip.
GAVE_UP_AT_EACH_DOOR = {
    "judge": "the last try stays in the ad, and the shop owner is shown every try and your reason.",
    "owner": "the scene keeps its clip as it was, and the shop owner is shown every try and "
    "your reason.",
}
GIVE_UP_FOR_A_NOTE = (
    "Give up on filming this scene: it keeps its clip as it was, and the shop owner is shown "
    "every try and your reason. Say why, and which try you think is best."
)
GIVEN_UP_AT_EACH_DOOR = {
    "judge": "The scene is given up: its last try stays in the ad, and the shop owner is "
    "shown every try and your reason.",
    "owner": "The scene is given up: it keeps its clip as it was, and the shop owner is shown "
    "every try and your reason.",
}


def test_the_fixing_agent_is_told_what_giving_up_does_at_each_door_word_for_word() -> None:
    assert fixing_agent.GAVE_UP == GAVE_UP_AT_EACH_DOOR
    assert fixing_agent.GIVE_UP_FOR_A_NOTE == GIVE_UP_FOR_A_NOTE
    assert fixing_agent.GIVEN_UP == GIVEN_UP_AT_EACH_DOOR
    for door, gave_up in GAVE_UP_AT_EACH_DOOR.items():
        told = fixing_agent.instructions(door)
        assert told.startswith(FIXING_AGENT_AS_APPROVED.replace("{gave_up}", gave_up))


def test_the_fixing_agent_is_told_the_apps_prompt_rules_unchanged() -> None:
    told = fixing_agent.instructions()
    assert told.startswith(
        FIXING_AGENT_AS_APPROVED.replace("{gave_up}", GAVE_UP_AT_EACH_DOOR["judge"])
    )
    assert BROLL_SHARED_RULES in told
    assert all(rules in told for rules in BROLL_KIND_RULES.values())


def test_the_fixing_agent_runs_on_the_deciders_model() -> None:
    assert MODEL_FOR_PURPOSE["fix_scene"] == MODEL_FOR_PURPOSE["decide_fixes"] == "gpt-6.1-sol"


# The first-send prompt (#210 point 16, decided; Julian's words in the decision record). It
# retires, for the first send, the frozen opener's "The camera stays still.", SAME_SIDE (A18)
# and reference mode's "Image 1 is how the clip starts": the tests above pin them while the old
# writer is still the one the clip step calls.
FIRST_SEND_FROZEN = [
    pytest.param(SPEED_LINE, "Film this in real-time speed.", id="A1 the speed line, first"),
    pytest.param(
        PIECE_LABEL, "only for the movement, not the look", id="a piece is for its movement"
    ),
    pytest.param(
        FIRST_SEND_INSTRUCTIONS,
        '- "sentence": ONE sentence for the whole clip, built from the scene and the pictures: '
        "the action, the thing it is done to or with, the place, and how it ends, said the way "
        "a person would say it. Give no timings.",
        id="one sentence: action, thing, place, end state",
    ),
]


@pytest.mark.parametrize(("text", "sentence"), FIRST_SEND_FROZEN)
def test_the_first_send_prompts_rules_are_as_decided_word_for_word(
    text: str, sentence: str
) -> None:
    assert sentence in text


def test_the_first_send_writer_is_told_nothing_of_the_retired_opener() -> None:
    # #210 point 16: gone from the first send are the camera opener, the side sentence, "Image
    # 1 is how the clip starts", the picture prompt and timings.
    for retired in ("The camera stays still", SAME_SIDE, "how the clip starts", "-second video"):
        assert retired not in FIRST_SEND_INSTRUCTIONS


@pytest.mark.parametrize("name", ["garlic", "press", "toilet", "phone case", "bag"])
def test_the_first_send_writers_rules_name_no_tested_product(name: str) -> None:
    # CODING_STANDARDS.md: the contract fixture's product and the graded rounds' products.
    assert not re.search(rf"\b{name}\b", FIRST_SEND_INSTRUCTIONS, re.IGNORECASE)
