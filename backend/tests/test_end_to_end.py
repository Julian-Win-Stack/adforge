"""Whole ads made through the chat, from the shop owner's first message to the finished ad.
The producer's model and every outside service are faked at the gateway, the shop is served
on this machine, and ffmpeg runs for real on tiny clips.

The mug's first ad: what scene 2 shows fails the fact check and is rewritten, the shop owner
has the script shortened to their target, and every scene is made and assembled. Its second
ad has a talking scene, two B-roll scenes, each made from the sent list cut-and-send builds
for it and never from a starting picture (spec #210 points 13-19), and a talking scene to end
on."""

import importlib.util
from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from agents.models import ToolCall
from agents.snapshot import AD_AS_IT_STANDS
from chat.sent import NOTHING_SENT
from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.library import SentList
from jobs.models import Job

from .conftest import (
    FIRST_SEND_SENTENCE,
    NO_CHOICES,
    PLAN,
    READABLE,
    SENT_LIST_JUDGED,
    HeldSteps,
    ScriptedProducer,
    broll,
    chat,
    colour_at,
    facts_ok,
    given_to_the_producer,
    handoffs,
    lines,
    loudness,
    paid_for,
    producer_turns,
    results_of,
)
from .test_producer_broll import BROLL_PLAN
from .test_producer_fold import fold_past, last_turn_tokens
from .test_producer_fold import sized as sized  # a fixture

# The lines of the mug's plan with scene 2 showing the mug.
BROLL_LINES = [scene["line"] for scene in BROLL_PLAN["plan"]["scenes"]]

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

# How the talking scenes' starting pictures are planned: the person holding the mug.
TALKING: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze.",
    "prompt": "She holds the mug up beside her face in her sunny workshop.",
    "prompt_reason": "Holding it by her face shows the mug off.",
}


def every_scene(tool: str, scenes: int = 3, **arguments: Any) -> list[tuple[str, dict[str, Any]]]:
    """A call of `tool` for each of the ad's `scenes` scenes."""
    return [(tool, {"scene": scene, **arguments}) for scene in range(1, scenes + 1)]


def chat_and_run(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    message: str,
    *answer: Turn | Callable[[], Turn],
) -> None:
    """The shop owner says `message`, and the producer answers with the turns in `answer`.
    Then every scene step it started runs, as a worker would, and the producer tells the
    shop owner once the last of them finishes."""
    fake_model.respond("produce", *answer)
    say(message)
    if steps.held:
        fake_model.respond("produce", turn(says="That part's ready."))
    steps.run_held()


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
        chat_and_run(fake_model, say, steps, message, *answer)

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


def test_the_b_roll_clips_prompt_is_written_from_its_rewritten_shows_after_the_shortening(
    ad_made_through_the_chat: None,
) -> None:
    # Written once, for scene 2's shortened line, showing what the rewrite made it show:
    # never the pour the page doesn't support.
    assert [
        (written["scene"]["line"], written["scene"]["shows"])
        for written in handoffs("write_first_send_prompt")
    ] == [(SHORTENED_2, "the mug turned slowly in a hand")]
    # The producer asked for scene 2's starting picture, and none was drawn (#210 point 19).
    assert list(
        Job.objects.get()
        .produced.filter(kind="starting_picture")
        .values_list("scene__number", flat=True)
    ) == [1, 3]


def test_nothing_in_the_whole_flow_is_paid_for_twice(ad_made_through_the_chat: None) -> None:
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
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
        # The talking scenes' starting pictures only: scene 2 has none.
        "choose_starting_picture",
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
        # Scene 2's clip: the ad's room, drawn from its before-picture, and the presenter
        # standing in it, once; its prompt written; no picture checked.
        "plan_before_picture",
        "make_before_picture",
        "make_room_alone",
        "make_person_in_room",
        "write_first_send_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *SENT_LIST_JUDGED,
        "make_talking_clip",
        "collect_talking_clip",
    ]


def test_the_shop_owner_is_never_told_which_scenes_are_b_roll(
    ad_made_through_the_chat: None, api: APIClient, session_id: str
) -> None:
    told = [text for role, text in chat(api, session_id) if role == "agent"]
    assert [text for text in told if "b-roll" in text.lower()] == []


# --- An ad with a B-roll scene that needs a photo of its own ------------------------------------

# The fake voice says 2 words a second. Scene 2's 8 words take 4 seconds, so its clip is
# asked for 5; scene 3's 11 take 5.5, so its clip is asked for 6.
TURNING = "Hand-thrown, holds 350 ml, and dishwasher safe too."
HANDLE = "Every mug is hand-thrown, so each one is a little different."

# What scene 3 needs that the mug's front can't show: its handle, which the page's photo 2,
# library item 2, shows.
HANDLE_PICTURES = [{"number": 2, "for": "in-use"}]

BOTH_WAYS: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        # The colour the page's photos are labelled in, so none is recoloured.
        "product_colour": "green",
        "scenes": [
            {"line": "Meet the Stoneware Mug.", "overlay": "Kiln & Co"},
            # Nothing it shows is missing from the main photo.
            broll({"line": TURNING, "shows": TURNED}),
            # It needs the handle from the side.
            broll(
                {
                    "line": HANDLE,
                    "shows": "the mug's handle, seen from the side, as a hand lifts it",
                    "scene_pictures": HANDLE_PICTURES,
                }
            ),
            {"line": "Yours today for just $24.00, from Kiln & Co.", "overlay": "$24.00"},
        ],
    },
}


@pytest.fixture
def ad_made_with_needs(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], steps: HeldSteps
) -> None:
    """A chat that took the mug's 4-scene ad, with two B-roll scenes, one of which needs a
    photo of the handle, from the shop owner's first message to the finished ad."""

    def chatting(message: str, *answer: Turn | Callable[[], Turn]) -> None:
        chat_and_run(fake_model, say, steps, message, *answer)

    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", BOTH_WAYS)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))
    chatting(
        f"Make a 15 second ad for {product_page_url}",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 15})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's your script. Shall I make it?"),
    )

    # The talking scenes' pictures: the B-roll scenes have none.
    fake_model.respond("choose_starting_picture", TALKING, TALKING)
    chatting(
        "Looks good, go ahead",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(
            calls=every_scene("make_starting_picture", scenes=4, note=None)
            + every_scene("make_line_audio", scenes=4)
        ),
        turn(says="The music is made, and every scene is on its way."),
    )
    chatting(
        "Keep going",
        turn(calls=every_scene("transcribe_line_audio", scenes=4)),
        turn(says="Listening to each line."),
    )
    chatting(
        "Keep going", turn(calls=every_scene("make_clip", scenes=4)), turn(says="Making clips.")
    )
    chatting("Put it together", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!"))


def test_every_b_roll_clip_is_sent_its_sent_list_and_no_starting_picture(
    ad_made_with_needs: None,
) -> None:
    # Spec #210 point 13: Image 1 is the front page photo, then the scene's pictures (scene 3's
    # handle), then the ad's room alone, as nobody is seen; never a starting picture.
    job = Job.objects.get()
    room = job.produced.get(kind="room_alone").file
    front, handle = (job.photos.get(position=position).file for position in (1, 2))
    asked = [
        (handoff["starting_picture"], handoff["example_pictures"], handoff["seconds"])
        for handoff in handoffs("make_broll_clip")
    ]
    assert asked == [(None, [front, room], 5), (None, [front, handle, room], 6)]
    # Point 16: the speed line, one line per picture, then the writer's one sentence.
    assert handoffs("make_broll_clip")[1]["prompt"].splitlines() == [
        "Film this in real-time speed.",
        "Image 1 is the product from the front, alone on a plain background.",
        "Image 2 is the product from the front, alone on a plain background.",
        "Image 3 is the room: it is the background.",
        FIRST_SEND_SENTENCE,
    ]
    # Each clip keeps the very list it was sent, and the scene picture is said as one.
    (_, handle_clip) = job.produced.filter(kind="clip", scene__number__in=[2, 3]).order_by(
        "scene__number"
    )
    sent = SentList.model_validate(handle_clip.sent)
    assert sent.prompt == handoffs("make_broll_clip")[1]["prompt"]
    assert [(picture.tier, picture.for_) for picture in sent.pictures] == [
        (1, None),
        (2, "in-use"),
        (3, None),
    ]
    # Neither B-roll scene got a starting picture.
    assert sorted(
        job.produced.filter(kind="starting_picture").values_list("scene__number", flat=True)
    ) == [1, 4]


def test_each_b_roll_clip_plays_whole_while_the_next_line_is_said_over_its_end(
    ad_made_with_needs: None,
) -> None:
    (ad,) = Job.objects.get().produced.filter(kind="finished_ad")
    # Scene 2's 5-second clip starts after scene 1's 2 seconds and plays whole. Scene 3's line
    # starts at 6, over its end, so scene 3's picture runs a second behind its line, plays
    # its 6 seconds whole, and scene 4 skips the start of its picture to catch up: its 9
    # words, said from 11.5, end at 16.
    assert [(cut["scene"], cut["start"], cut["end"]) for cut in ad.cuts] == [
        (1, 0.0, 2.0),
        (2, 2.0, 7.0),
        (3, 7.0, 13.0),
        (4, 13.0, 16.0),
    ]
    heard = read(ad.file)
    # The fake's clips are red, lime, blue and yellow in the order they were asked for.
    assert [colour_at(heard, at) for at in (1.0, 4.5, 10.0, 14.5)] == [
        "red",
        "lime",
        "blue",
        "yellow",
    ]
    # The voice never stops: scene 3's line is heard over the end of scene 2's clip.
    assert loudness(heard, "voice", between=(6.2, 6.8)) == pytest.approx(-17, abs=1)


def test_the_ad_with_a_scene_needing_a_photo_pays_for_each_thing_once(
    ad_made_with_needs: None,
) -> None:
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
        "plan_ad",
        "draw_person",
        "design_voice",
        "measure_voice",
        "fact_check",
        "make_music",
        # Only the talking scenes have starting pictures, and no picture is checked.
        "choose_starting_picture",
        "make_starting_picture",
        "choose_starting_picture",
        "make_starting_picture",
        "speak_line",
        "speak_line",
        "speak_line",
        "speak_line",
        "transcribe_line",
        "transcribe_line",
        "transcribe_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
        # One before-picture, room alone and presenter in it for both B-roll scenes.
        "plan_before_picture",
        "make_before_picture",
        "make_room_alone",
        "make_person_in_room",
        "write_first_send_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *SENT_LIST_JUDGED,
        "write_first_send_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *SENT_LIST_JUDGED,
        "make_talking_clip",
        "collect_talking_clip",
    ]


def test_each_clip_in_the_chat_carries_what_it_was_sent(
    ad_made_through_the_chat: None, api: APIClient, session_id: str
) -> None:
    conversation = api.get(f"/api/sessions/{session_id}/").json()["messages"]
    shown = {
        attachment["scene"]: attachment["sent"]
        for message in conversation
        for attachment in message["attachments"]
        if attachment["kind"] == "video" and attachment["scene"] is not None
    }
    # The talking clips were sent no list; the B-roll clip shows the one it was sent.
    assert shown[1] == shown[3] == NOTHING_SENT
    clip = Job.objects.get().produced.get(kind="clip", scene__number=2)
    assert clip.sent is not None
    assert (
        [each["label"] for each in shown[2]["pictures"]]
        == [picture["label"] for picture in clip.sent["pictures"]]
        == [
            "The product from the front, alone on a plain background.",
            "The room: it is the background.",
        ]
    )
    assert shown[2]["prompt"] == handoffs("make_broll_clip")[0]["prompt"]


# --- Spec #247: the first message, the plan first, the fold, nothing of the old path ----------


def first_ad_from_one_link(producer: ScriptedProducer, fake_model: FakeModel, link: str) -> None:
    """The shop owner sends the mug's link and nothing else, and the producer, scripted, goes
    the usual way to the finished ad (spec #247 point 3): read the page, plan, the person,
    the checks, the music, then every scene's steps as the worker finishes them, and the ad
    assembled. Scene 2 shows the mug, so its clip is judged."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", BROLL_PLAN)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("choose_starting_picture", TALKING, TALKING)
    producer.told(
        link,
        ("read_page", {"link": link, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "light upbeat lo-fi"}),
        [
            ("make_starting_picture", {"scene": 1, "note": None}),
            ("make_starting_picture", {"scene": 3, "note": None}),
            *every_scene("make_line_audio"),
        ],
        "I'm making your ad.",
    )
    # The worker makes each step; the producer, woken once the last running one finishes,
    # starts the next ones.
    for _ in range(4):
        producer.finished_meanwhile()
    producer.woken(every_scene("transcribe_line_audio"))
    for _ in range(2):
        producer.finished_meanwhile()
    producer.woken(every_scene("make_clip"))
    for _ in range(2):
        producer.finished_meanwhile()
    producer.woken(("assemble_ad", {}), "Here's your ad!")


# What a picture, an audio or a clip is paid for under.
MAKES = {
    "draw_person",
    "make_starting_picture",
    "speak_line",
    "make_talking_clip",
    "make_broll_clip",
    "make_before_picture",
    "make_room_alone",
    "make_person_in_room",
}


def test_one_link_with_no_instructions_makes_the_whole_ad_with_no_question_and_no_plan_card(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    """The producer is scripted, so this proves no code asks a question, shows a plan card or
    stops the usual order; that the producer's instructions set the path is pinned in
    test_frozen_rules.py."""
    first_ad_from_one_link(producer, fake_model, product_page_url)

    job = Job.objects.get()
    (ad,) = job.produced.filter(kind="finished_ad")
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3]
    # Every B-roll clip is judged: scene 2's, once.
    assert paid_for().count("check_clip_motion") == 1
    # Nobody asked the shop owner anything: one message from them, and no question.
    assert not ToolCall.objects.filter(tool="ask_owner").exists()
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert [message["role"] for message in messages].count("user") == 1
    # No plan card: nothing the chat is sent is a plan to say yes to.
    assert all("plan" not in message for message in messages)
    assert "I'm making your ad." in producer.said()
    assert producer.said()[-1] == "Here's your ad!"
    # The ad is shown in the chat.
    assert any(
        attachment["kind"] == "video" and attachment["scene"] is None
        for message in messages
        for attachment in message["attachments"]
    )


def test_show_me_the_plan_first_shows_the_plan_and_makes_nothing_until_the_next_message(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    """The producer is scripted, so this proves no code makes anything after the plan or
    stops the next message going on; no sentence of its instructions names this redirect
    (Julian's to word)."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", BROLL_PLAN)

    producer.told(
        f"{product_page_url} Show me the plan before you make anything.",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("plan_ad", {}),
        lambda: turn(says="Here's the plan:\n" + "\n".join(lines())),
    )

    # The plan is shown, in the producer's words.
    assert producer.said()[-1] == "Here's the plan:\n" + "\n".join(BROLL_LINES)
    # No picture, audio or clip is made: no person drawn, no voice, no scene step.
    assert MAKES.isdisjoint(paid_for())
    assert fake_model.pictures_drawn_on == fake_model.spoken == fake_model.clips_submitted == []
    assert producer.steps.held == []
    assert not Job.objects.get().produced.exists()

    # The next message carries on from the plan.
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    producer.told(
        "Looks good, make it",
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        every_scene("make_line_audio"),
    )
    assert "draw_person" in paid_for()
    assert len(producer.steps.held) == 3


def test_past_half_the_window_a_long_session_folds_its_oldest_calls_and_the_next_turn_fits(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    sized: None,
    settings: Settings,
) -> None:
    first_ad_from_one_link(producer, fake_model, product_page_url)
    assert not ToolCall.objects.exclude(fold_line="").exists()
    # The fold point is set where the whole first ad, given in full, just reaches it, as a
    # small setting stands in for half the model's window.
    point = last_turn_tokens()
    fold_past(settings, point)
    said_before = producer.said()

    producer.told("How much has it cost?", ("cost_so_far", {}), "It has cost a few dollars.")

    # The oldest calls folded to one line each, kept on the row: the page read and the plan
    # among them.
    folded = {call.tool for call in ToolCall.objects.exclude(fold_line="")}
    assert {"read_page", "plan_ad"} <= folded
    (read_page,) = ToolCall.objects.filter(tool="read_page")
    assert read_page.fold_line.startswith("read_page: made") and "\n" not in read_page.fold_line
    # Nothing is deleted: the row keeps its whole answer.
    assert len(read_page.result) > len(read_page.fold_line)
    assert ToolCall.objects.get(tool="cost_so_far").fold_line == ""
    # The producer's next turn was given the folded line, and the ad as it stands, last.
    (given,) = [
        each["result"]
        for each in given_to_the_producer(producer_turns())
        if each.get("tool") == "read_page"
    ]
    assert given.startswith(read_page.fold_line)
    last = handoffs("produce")[-1]
    assert last["snapshot"].startswith(AD_AS_IT_STANDS)
    # It fits: the turn after the fold is under the fold point.
    assert last_turn_tokens() < point
    # The shop owner sees nothing of it.
    assert producer.said() == [*said_before, "It has cost a few dollars."]


def test_nothing_of_the_old_path_remains_a_note_after_the_ad_is_the_producers_to_answer(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    first_ad_from_one_link(producer, fake_model, product_page_url)
    paid_before = paid_for()

    # A note once the ad has its clips, then a yes sent as the old plan card sent it.
    producer.told("Scene 2 should feel calmer.", "Shall I make scene 2's action calmer?")
    fake_model.respond("produce", turn(says="I'll make it calmer."))
    sent = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "Yes", "plan": 1}, format="json"
    )
    assert sent.status_code == 201, sent.json()

    # Neither was routed to another agent: the producer's own turns answered both, as words,
    # and nothing else was paid for.
    assert fake_model.left("produce") == 0
    assert producer.said()[-2:] == ["Shall I make scene 2's action calmer?", "I'll make it calmer."]
    assert paid_for() == paid_before
    assert given_to_the_producer(producer_turns())[-2:] == [
        {"kind": "said", "by": "agent", "text": "Shall I make scene 2's action calmer?"},
        {"kind": "said", "by": "user", "text": "Yes"},
    ]
    # The old path's code is gone, not skipped.
    for module in ("agents.routing", "agents.run_plan", "agents.decider", "jobs.fix_plans"):
        assert importlib.util.find_spec(module) is None, module
    # The chat is sent no plan and no yes to tap.
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert all("plan" not in message for message in messages)
