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
    PLAN_INSTRUCTIONS,
)
from jobs.scenes import (
    BEFORE_PICTURE_INSTRUCTIONS,
    PERSON_IN_ROOM_PROMPT,
    ROOM_ALONE_PROMPT,
    ROOM_FROM_PERSON_PROMPT,
)

PLANNER = PLAN_INSTRUCTIONS


def test_h3_kind_choice_and_line_length_rules_stay_as_approved() -> None:
    # "At least one B-roll" starts at three scenes: in a two-scene ad the last scene talking
    # wins (Julian, Nov 8 2026, thread "Build emotional after in ads").
    assert (
        "The first scene is always the person talking to camera. The scenes between the first "
        "and the last are B-roll by default: one of them may be talking only in the style "
        '"talking while doing it", with her using the product on herself. Every ad of three or '
        "more scenes has at least one B-roll scene, centred on the product. When a real demo "
        "is hard, "
        "film the moment before, the feature up close or the feeling after, such as floss "
        "pulled out of its box and drawn in and out. For each scene ask: can she show "
        "what this line says with the product in her hands or on herself, at arm's length? If yes, "
        'choose kind "talking": her face is in frame and she talks while showing the product, '
        "using it, or showing its result. Showing the product to the lens counts as its action; "
        "never a plain talking head. Only the first or the last scene may just show the product "
        "to the lens: set shows_to_lens true for a scene whose action only shows the product to "
        'the lens. If no, choose kind "broll". A B-roll scene never shows her face: it may show '
        "any part of her body with the product, such as her hands, feet, legs, wrist or neck, "
        "and her voice speaks off camera. Give kind_why: what her hands cannot show. "
        "For either kind, shows names its one action with the product, never null. The action and "
        "every claim must come from the page, the product photos or the shop owner's words. "
        "The first scene stays talking even when it can only show the product to the lens. "
    ) in PLAN_INSTRUCTIONS
    assert (
        "A B-roll scene has no talking style or product state. "
        "With no target length, plan 3 or 4 scenes: the hook, 1 or 2 body scenes and the call to "
        "action, aiming for about 30 seconds, never over 35 seconds in whole clips and the end "
        # 2.8 words a second: spec #401 (#407), same speech time per line as before.
        "card. Write lines of about 13 to 18 words at 2.8 words a second. With a target, use as "
        "many scenes as it needs and fit the owner's length, which counts the end card too. "
        "Every line must take at least 4 and at most 14 seconds (12 to 39 words) at that speed. "
        "H3 makes the voice from the same voice description in every clip; no voice file is made "
        "in advance. Each action fills the clip and ends with an action after the last word. "
        "The one-action rule below applies to both kinds. A B-roll scene's action is in no other "
        "scene; talking scenes may share one, such as the first and the last scene both showing "
        "the product to the lens.\n"
    ) in PLAN_INSTRUCTIONS
    assert BROLL_ONE_ACTION_INSTRUCTIONS in PLAN_INSTRUCTIONS
    assert "Every scene between them is a B-roll scene" not in PLAN_INSTRUCTIONS
    assert '"has face"' not in PLAN_INSTRUCTIONS


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


def test_the_person_in_room_picture_is_ordered_to_show_the_presenter_relaxed_only() -> None:
    # Not graded yet, frozen as Julian ordered it (Oct 28 17:37, "just standing straight in
    # that room. that's it."; spec #210 point 12): an order, not a ban. The relaxed pose and
    # the realism words replace "stands straight and still, hands at their sides" (Julian,
    # Nov 6, spec #401: she looked pasted on).
    assert (
        "The person stands in the room in a relaxed, natural pose, and nothing else happens."
    ) in PERSON_IN_ROOM_PROMPT
    assert (
        "lit only by its own lights: no ring light, no studio light, no beauty filter and no "
        "background blur."
    ) in PERSON_IN_ROOM_PROMPT


def test_the_room_alone_is_ordered_to_hold_only_the_room() -> None:
    # Not graded yet, frozen as Julian decided it (spec 2 item 53, Oct 28 22:32): "the
    # room-alone picture holds only the room, nothing of the scene".
    assert (
        "Keep the room itself as it is: its walls, floor, surfaces and fittings. Take out every "
        "other thing, and every mark, stain or mess on what stays, so nothing of any scene is "
        "left."
    ) in ROOM_ALONE_PROMPT
    # The room taken out of her picture keeps it word for word (spec #401).
    assert (
        "Keep the room itself as it is: its walls, floor, surfaces and fittings. Take out every "
        "other thing, and every mark, stain or mess on what stays, so nothing of any scene is "
        "left."
    ) in ROOM_FROM_PERSON_PROMPT


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


# #350 retires the three motion questions with Julian's approval. PRODUCT's question,
# model and frame rates remain pinned below.
def test_motion_only_describes_the_clip_and_has_no_quality_questions() -> None:
    assert set(clip_judge.MotionVerdict.model_fields) == {"look", "ordered_action", "seen_action"}
    for question in ("real_motion", "sudden_change", "does_what_was_ordered"):
        assert question not in MOTION_CHECK
    assert '"fail"' not in MOTION_CHECK


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
    "moves, and to describe what you see."
)


def test_a_clip_with_its_sent_list_is_judged_with_the_judge_briefs_words() -> None:
    pinned = Path(__file__).parent / "fixtures" / "clip_judge_product_check.txt"
    assert clip_judge.PRODUCT_CHECK == pinned.read_text()
    proved_head = MOTION_CHECK[: MOTION_CHECK.index("You may also be shown")]
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
    # #350: motion describes the clip and checks an owner change; only PRODUCT judges it.
    # #359 adds transcript guidance and heard words only to the fix question.
    # #365 asks whether an unshown state was requested before skipping PRODUCT.
    assert clip_judge.wording_version() == "be53981ef3"
    assert clip_judge.wording_version(fix=True) == "b27941cbe0"
    # Spec #303 changes the inputs, while the questions and old-clip wording stay put.
    assert clip_judge.wording_version(scene=True) == "a9ad431496"
    assert clip_judge.wording_version(scene=True, fix=True) == "e382da5005"
    # Spec #327: talking clips expect a moving mouth, without changing the judge's questions.
    assert clip_judge.TALKING_SCENE_CONTEXT == (
        "The woman talks on camera in this clip; her mouth moving is expected."
    )
    assert clip_judge.wording_version(scene=True, talking=True) == "66af506b0d"
    assert clip_judge.wording_version(scene=True, talking=True, fix=True) == "dc29ff6869"


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
    # Julian's round 2 rule (Oct 9), back in place of the proof rule with his OK (#437,
    # Nov 7 2026).
    "Every B-roll scene does something with the product that sells it: a hand only holding, "
    "placing, setting down, standing up or pointing at the product isn't B-roll, except a "
    "still shot of it when the line is about exactly where it fits or how it looks.",
    "Finishing steps such as putting it away or wiping up get no clip.",
    # Narrowed to product actions with Julian's OK (Nov 8 2026, thread "Design ad hook and
    # emotional after"): the fails behind it were product steps (Oct 6 bag, Oct 8 curler).
    "One product action per clip, except movements the owner explicitly asks for in their "
    "order; every redo keeps every step they asked for. A product action moves the product "
    "or changes its shape or state; her face, head and free-hand moves and camera moves "
    "don't count.",
    "A two-state claim needs two one-action clips joined by a cut.",
    "Always draw a starting picture: the starting picture is an order the clip must follow.",
    "Photos are for looks only; copying their pose is not their job.",
    "Use real-time speed.",
    "Never show a side or part no photo shows.",
    "A result shows only where the product touches.",
    "In a problem scene nobody touches the product.",
]


@pytest.mark.parametrize("sentence", SCENE_PROVEN_RULES)
def test_the_live_scene_agent_keeps_the_other_proven_rules_word_for_word(sentence: str) -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    assert sentence in " ".join(INSTRUCTIONS.split())


# #371, approved Nov 4: short negatives may support a positive description, and
# counted action beats are distinct from spoken numbers and clock times.
VIDEO_FORM_NEGATIVES = (
    "Describe what should happen in the video form; a short 'no X' may back it up."
)
VIDEO_FORM_COUNTED_BEATS = (
    "Keep spoken-style numbers and clock times out of the action description; "
    "a counted action beat, such as after the third bounce, is allowed."
)


def test_the_scene_agent_allows_supporting_negatives_and_counted_action_beats() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_forms import VideoForm

    assert VIDEO_FORM_NEGATIVES in " ".join(INSTRUCTIONS.split())
    assert VIDEO_FORM_COUNTED_BEATS in " ".join((VideoForm.__doc__ or "").split())
    assert "code refuses not" not in INSTRUCTIONS
    assert "no lists of what must not happen" not in (VideoForm.__doc__ or "")


# #374, approved Nov 4: know-how is fixed in the instructions and tool schema,
# before the agent fills the tool, rather than added after every failed clip.
# #408, approved Nov 6: end pictures from the first try, with the four cases as guidance.
END_PICTURE_POINTER = (
    "make_end_picture makes the clip's last frame from the starting picture, from the first "
    "try on: for a result where the product touched, a push-in ending on a crop of the real "
    "label, a clip that drifts or ends in the wrong place, or an action that must stop at a "
    "set point. These are examples, not a limit; follow its notes."
)
END_PICTURE_NOTE = (
    "Edit the starting picture into an end picture: the clip's last frame, sent to H3 with "
    "the starting picture. Use it, from the first try on, when the clip must end on a result "
    "where the product touched; when a push-in must end on a crop of the real label; when the "
    "clip drifts or ends in the wrong place; or when the action must stop at a set point, such "
    "as a press that stops part way down. These are examples, not a limit. "
    "Each run has six end-picture tries, separate from its six starting-picture tries."
)
END_PICTURE_BOXES = {
    "keep": "Everything else stays exactly as in the starting picture; name what stays. "
    "For a result, the product or hand stays on the spot that changes.",
    "change": "Change only what the clip's last frame must show: the result at the spot "
    "the product touched, the crop of the real label, where the clip ends, or where the "
    "action stops; say what it becomes.",
}
END_PICTURE_FILM_BOXES = {
    "beats": "With an end picture, the hand keeps working at the changing spot, finish the "
    "change at an early counted beat, and say what happens after.",
    "shows_up_later": "With an end picture, describe the result as a finished state, backed "
    "by a short 'no X', rather than a gradual quality.",
    "after_last_word": "With an end picture, end in the pose the end picture shows.",
}


def test_end_picture_guidance_stays_in_the_instructions_and_tool_boxes() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_agent.make_end_picture import MakeEndPicture
    from agents.scene_forms import VideoForm

    assert END_PICTURE_POINTER in " ".join(INSTRUCTIONS.split())
    assert " ".join(MakeEndPicture.spec().description.split()) == END_PICTURE_NOTE
    for name, description in END_PICTURE_BOXES.items():
        assert MakeEndPicture.model_fields[name].description == description
    for name, description in END_PICTURE_FILM_BOXES.items():
        assert description in (VideoForm.model_fields[name].description or "")


H3_SCENE_RULES = [
    # #373: starting/end pictures use reference roles beside a page-video piece.
    "H3 receives the starting picture and its end picture when one exists. For a "
    "page-video scene, they go as Image 1 and Image 2 beside the page-video piece; "
    "without an end picture, only Image 1 goes beside it.",
    "A talking scene has her face in frame and shows the product in her hands, on her body, "
    "or its result while she says the line on camera.",
    # Widened from "hands and product only" with Julian's OK (Nov 8 19:03, round 12 Crocs).
    "A B-roll scene never shows her face: it may show any part of her body with the product, "
    "such as her hands, feet, legs, wrist or neck, and her voice speaks off camera.",
    # Julian, Nov 8 (round 12 Crocs): a worn product copies the page photo's outfit.
    "When the product is worn, draw it as a page photo shows it worn (pick that photo) and "
    "copy everything from that photo except the face, the room and the pose: the outfit, "
    "the styling and how it is worn. Never make up an outfit. The room stays the shared "
    "room, and the pose follows the scene's action.",
    # Narrowed with Julian's OK (Nov 6 05:59, spec #401): only the first or the last scene may
    # just show the product to the lens.
    "Showing the product to the lens counts as the action in a talking scene, but only the "
    "first or the last scene may just show the product to the lens.",
    "For a failing talking clip, first fix the cause the judge names; if the movement keeps "
    "failing, use a simpler action that still proves the line; last, show the product to "
    "the lens while she says the line, but only the first or the last scene may just show the "
    "product to the lens.",
]


@pytest.mark.parametrize("sentence", [*H3_SCENE_RULES, SCENE_PROVEN_RULES[0]])
def test_h3_scene_agents_keep_the_approved_kind_and_fallback_rules(sentence: str) -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    assert sentence in " ".join(INSTRUCTIONS.split())


def test_the_picture_check_keeps_its_two_approved_questions_word_for_word() -> None:
    """End pictures keep both as approved. A starting picture swaps question 1's last
    sentence for question 4, the "side or part no photo shows" rule (Julian, Nov 6 06:13).
    Both forgive very tiny letters but not the brand name or main logo words (Julian, Nov 8)."""
    from agents.scene_agent.picture_check import instructions

    end = " ".join(instructions(starting=False).split())
    starting = " ".join(instructions(starting=True).split())
    question_1 = (
        "1. Does the product, with what is inside it, look the same as in its photos: its shape, "
        "colours, parts, printed words and contents? Where the photos don't show a part, the "
        "page's words say how it looks. Very tiny letters, such as the fine print on a label, "
        "don't fail it. Big letters, such as the brand name and the main logo words, must still "
        "match."
    )
    assert f"{question_1} A part the photos never show, drawn plausibly, doesn't fail it." in end
    assert f"{question_1} Question 4 covers sides and parts the photos never show." in starting
    question_2 = (
        "2. Does anything in the picture break physics: something no real camera could film, "
        "such as a person brushing hair with no head under it, a hand that isn't joined to an arm, "
        "an object floating, or a thing passing through another? A slightly wrong hand "
        "doesn't fail it."
    )
    assert question_2 in end
    assert question_2 in starting


def test_the_live_planner_and_scene_agent_allow_camera_movement_beside_the_action() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    for words in (PLAN_INSTRUCTIONS, INSTRUCTIONS):
        assert "The camera never moves" not in words
        assert "never move the camera" not in words
    assert "A camera movement may accompany that action; it does not replace it." in " ".join(
        PLAN_INSTRUCTIONS.split()
    )


def test_planner_and_picture_form_share_the_label_words_and_the_check_shares_open_and_side() -> (
    None
):
    """Ticket #406: each rule reads the same wherever it is told."""
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_agent.picture_check import instructions
    from agents.scene_forms import PictureForm, VideoForm
    from jobs.picture_rules import LABEL_RULE, OPEN_RULE, PLAN_OPEN_RULE, SIDE_RULE

    check = instructions(starting=True)
    form = " ".join(str(field.description) for field in PictureForm.model_fields.values())
    assert OPEN_RULE in check
    for rule in (LABEL_RULE, OPEN_RULE):
        assert rule in form
    # Ticket #427: the label words are told to the planner and the form, never checked.
    assert LABEL_RULE not in check
    assert LABEL_RULE in PLAN_INSTRUCTIONS
    assert PLAN_OPEN_RULE in PLAN_INSTRUCTIONS
    assert SIDE_RULE in check
    assert SIDE_RULE in form
    assert SIDE_RULE in (VideoForm.model_fields["camera"].description or "")
    assert SIDE_RULE in " ".join(INSTRUCTIONS.split())
    assert SIDE_RULE in " ".join(PLAN_INSTRUCTIONS.split())
    assert SIDE_RULE in BROLL_ONE_ACTION_INSTRUCTIONS
    end = instructions(starting=False)
    assert LABEL_RULE not in end
    assert OPEN_RULE not in end


def test_the_picture_form_asks_for_a_natural_grip_and_the_check_does_not() -> None:
    """Round 12: 13 of 19 hold-to-lens starting pictures pinched the product by a fingertip
    or an edge to keep the label clear. Julian (Nov 8): the drawer asks for a natural grip
    that fits the product's size; no new check question."""
    from agents.scene_agent.picture_check import instructions
    from agents.scene_forms import PictureForm
    from jobs.picture_rules import GRIP_RULE

    assert GRIP_RULE in (PictureForm.model_fields["pose"].description or "")
    assert GRIP_RULE not in instructions(starting=True)
