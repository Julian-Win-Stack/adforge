"""One ad with a B-roll scene, made through the chat from the shop owner's first message to
the finished ad: what scene 2 shows fails the fact check and is rewritten, the shop owner
has the script shortened to their target, and every scene is made and assembled. The
producer's model and every outside service are faked at the gateway, the shop is served on
this machine, and ffmpeg runs for real on tiny clips."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.file_store import read
from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import Job

from .conftest import (
    NO_CHOICES,
    PLAN,
    READABLE,
    HeldSteps,
    broll,
    chat,
    colour_at,
    facts_ok,
    handoffs,
    loudness,
    paid_for,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# What scene 2 shows as planned, which the page doesn't support, and once rewritten.
POUR = "tea poured from a teapot into the mug"
TURNED = "the mug turned slowly in a hand"

# Scene 2's line: its 8 words take the fake voice, at 2 words a second, the 4 seconds a
# B-roll line takes at least.
SAID_OVER = "Hand-thrown, holds 350 ml, and dishwasher safe too."
# Scene 2's line once the script is shortened, also 8 words.
SHORTENED_2 = "It's hand-thrown, holds 350 ml, and dishwasher safe."

# The mug's plan, with scene 2 a B-roll scene. Its 19 words take the fake voice 9.5 seconds
# to say: over the shop owner's 5-second target.
PLANNED: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            {"line": "Meet the Stoneware Mug from Kiln & Co.", "overlay": "Kiln & Co"},
            broll({"line": SAID_OVER, "shows": POUR}),
            {"line": "Yours for $24.00.", "overlay": "$24.00"},
        ],
    },
}

# The fact check of the planned script: nothing on the page supports scene 2's pour.
POUR_UNSUPPORTED: dict[str, Any] = {
    "decision": "checked",
    "reason": "The page doesn't mention tea.",
    "question": None,
    "lines": [
        *facts_ok(1, 3)["lines"],
        {
            "scene": 2,
            "verdict": "wrong",
            "wrong": "shows",
            "problem": "The page doesn't mention tea.",
            "page_says": "The page doesn't mention it.",
        },
    ],
}

# The script shortened with every scene kept: 14 words, 7 seconds, within the 2 seconds over
# the target it may run. Scene 2's 8 words take 4 seconds, the least a B-roll line may take.
SHORTENED: dict[str, Any] = {
    "lines": [
        {"scene": 1, "line": "Meet the Stoneware Mug."},
        {"scene": 2, "line": SHORTENED_2},
        {"scene": 3, "line": "Just $24.00."},
    ]
}

# How the starting pictures are planned: the person holding the mug, or for scene 2, what
# it shows.
TALKING: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze.",
    "prompt": "She holds the mug up beside her face in her sunny workshop.",
    "prompt_reason": "Holding it by her face shows the mug off.",
}
SHOWING: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze as it turns.",
    "prompt": "A hand turns the mug slowly on a workbench in a sunny workshop.",
    "prompt_reason": "It shows what the scene describes.",
    "motion_prompt": "The hand turns the mug slowly; the camera holds still.",
    "motion_prompt_reason": "Turning it shows the mug from every side.",
}


def every_scene(tool: str, **arguments: Any) -> list[tuple[str, dict[str, Any]]]:
    """A call of `tool` for each of the ad's three scenes."""
    return [(tool, {"scene": scene, **arguments}) for scene in (1, 2, 3)]


def asking_what_the_checks_ask() -> Turn:
    """The producer's reply once the planning checks hand back a question for the shop
    owner: it asks them that question, as it is told to."""
    question, _ = results_of("run_planning_checks")[-1].split(" Why: ", 1)
    return turn(says=question)


@pytest.fixture
def ad_made_through_the_chat(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], steps: HeldSteps
) -> None:
    """A chat that took the mug's ad from the shop owner's first message to the finished ad."""

    def chatting(message: str, *answer: Turn | Callable[[], Turn]) -> None:
        """The shop owner says `message`, and the producer answers with the turns in `answer`.
        Then every scene step it started runs, as a worker would, and the producer tells the
        shop owner as each one finishes."""
        fake_model.respond("produce", *answer)
        say(message)
        fake_model.respond("produce", *[turn(says="That part's ready.") for _ in steps.held])
        steps.run_held()

    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLANNED)
    # Scene 2's pour fails the fact check, so it is rewritten to show something the page
    # supports, and checked again.
    fake_model.respond("fact_check", POUR_UNSUPPORTED, facts_ok(2))
    fake_model.respond(
        "rewrite_line",
        broll({"line": SAID_OVER, "shows": TURNED}),
    )
    chatting(
        f"Make a 5 second ad for {product_page_url}",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 5})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        asking_what_the_checks_ask,
    )

    # Every shortened line is new, so each is checked again.
    fake_model.respond("shorten_script", SHORTENED)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    chatting(
        "Shorten it",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "length_choice": "shorten"})]),
        turn(says="Done: your script fits 5 seconds now."),
    )

    fake_model.respond("choose_starting_picture", TALKING, TALKING)
    fake_model.respond("choose_broll_picture", SHOWING)
    chatting(
        "Looks good, go ahead",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(
            calls=every_scene("make_starting_picture", note=None) + every_scene("make_line_audio")
        ),
        turn(says="The music is made, and every scene is on its way."),
    )
    chatting(
        "Keep going",
        turn(calls=every_scene("transcribe_line_audio")),
        turn(says="Listening to each line."),
    )
    chatting("Keep going", turn(calls=every_scene("make_clip")), turn(says="Making the clips."))
    # The producer asks for every clip again before putting the ad together, as a confused
    # model might.
    chatting(
        "Put it together",
        turn(calls=[*every_scene("make_clip"), ("assemble_ad", {})]),
        turn(says="Here's your ad!"),
    )


def test_the_b_roll_scene_plays_in_its_turn_with_the_voice_heard_over_it(
    ad_made_through_the_chat: None,
) -> None:
    ads = Job.objects.get().produced.filter(kind="finished_ad")
    # Scene 2's 5-second clip plays whole, after scene 1, past its shortened line's 4
    # seconds: scene 3's 1-second line is said over its end, so none of scene 3's picture
    # shows.
    assert [(cut["scene"], cut["start"], cut["end"]) for ad in ads for cut in ad.cuts] == [
        (1, 0.0, 2.0),
        (2, 2.0, 7.0),
        (3, 7.0, 7.0),
    ]
    heard = read(ads.get().file)
    # Halfway through, it shows its own clip: the fake's clips are red, lime and blue in the
    # order they were asked for, and scene 2's was second.
    assert colour_at(heard, 3.25) == "lime"
    # The voice saying the line is heard over it, as loud as the fake voice speaks: silence
    # is about -91 dB.
    assert loudness(heard, "voice", between=(2.5, 4.0)) == pytest.approx(-17, abs=1)


def test_the_b_roll_scenes_picture_is_planned_from_its_rewritten_shows_after_the_shortening(
    ad_made_through_the_chat: None,
) -> None:
    # Planned once, for scene 2's shortened line, showing what the rewrite made it show:
    # never the pour the page doesn't support.
    assert [
        (planned["scene"], planned["line"], planned["shows"])
        for planned in handoffs("choose_broll_picture")
    ] == [(2, SHORTENED_2, "the mug turned slowly in a hand")]


def test_nothing_in_the_whole_flow_is_paid_for_twice(ad_made_through_the_chat: None) -> None:
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "plan_ad",
        "draw_person",
        "design_voice",
        "measure_voice",
        # The whole script, then scene 2 alone once what it shows was rewritten.
        "fact_check",
        "rewrite_line",
        "fact_check",
        # The shortened script's new lines.
        "shorten_script",
        "fact_check",
        "make_music",
        # Scene 2's starting picture is planned from what it shows.
        "choose_starting_picture",
        "make_starting_picture",
        "choose_broll_picture",
        "make_starting_picture",
        "choose_starting_picture",
        "make_starting_picture",
        "speak_line",
        "speak_line",
        "speak_line",
        "transcribe_line",
        "transcribe_line",
        "transcribe_line",
        # Each clip once, though the producer asked for every one again.
        "make_talking_clip",
        "collect_talking_clip",
        "make_broll_clip",
        "collect_broll_clip",
        "make_talking_clip",
        "collect_talking_clip",
    ]


def test_the_shop_owner_is_never_told_which_scenes_are_b_roll(
    ad_made_through_the_chat: None, api: APIClient, session_id: str
) -> None:
    told = [text for role, text in chat(api, session_id) if role == "agent"]
    assert [text for text in told if "b-roll" in text.lower()] == []
