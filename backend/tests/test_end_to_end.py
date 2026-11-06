"""Whole ads made through the chat, from the shop owner's first message to the finished ad.
The producer's model and every outside service are faked at the gateway, the shop is served
on this machine, and ffmpeg runs for real on tiny clips.

The mug's first ad: what scene 2 shows fails the fact check and is rewritten, the shop owner
has the script shortened to their target, and every scene is made and assembled. Its second
ad has two B-roll scenes made by scene agents from checked starting pictures, between
talking scenes. A one-link case starts all scene agents before any runs."""

import importlib.util
from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from agents.models import ToolCall
from agents.snapshot import AD_AS_IT_STANDS
from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.assembly import website_of
from jobs.library import SentList
from jobs.models import Job
from jobs.planning import END_CARD_SECONDS

from .conftest import (
    NO_CHOICES,
    READABLE,
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
    shows_the_end_card,
    video,
)
from .scene_script import script_scene_agents
from .test_h3_planning import h3_plan
from .test_producer_broll import BROLL_PLAN
from .test_producer_fold import fold_past, last_turn_tokens
from .test_producer_fold import sized as sized  # a fixture

# The lines of the mug's plan with scene 2 showing the mug.
BROLL_LINES = [scene["line"] for scene in BROLL_PLAN["plan"]["scenes"]]

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

# What scene 2 shows as planned, which the page doesn't support, and once rewritten.
POUR = "tea poured from a teapot into the mug"
TURNED = "the mug turned slowly in a hand"

# Scene 2's original and shortened lines both fit the shared H3 speech limits.
SAID_OVER = h3_plan()["plan"]["scenes"][1]["line"]
SHORTENED_2 = "This hand-thrown mug holds your favourite hot drink at home every day."

# The original script exceeds the owner's 15-second target.
PLANNED = h3_plan()
PLANNED["plan"]["scenes"][1]["shows"] = POUR

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

# Keep every scene while bringing its speech within the owner's target.
SHORTENED: dict[str, Any] = {
    "lines": [
        {
            "scene": 1,
            "line": "Meet the Stoneware Mug for your favourite hot drinks today, every day.",
        },
        {"scene": 2, "line": SHORTENED_2},
        {"scene": 3, "line": "Enjoy your mug at home and order from the shop every day."},
    ]
}

# Historical helper data retained for importing fixture modules.
TALKING: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze.",
    "prompt": "She holds the mug up beside her face in her sunny workshop.",
    "prompt_reason": "Holding it by her face shows the mug off.",
}


def every_scene(tool: str, scenes: int = 3, **arguments: Any) -> list[tuple[str, dict[str, Any]]]:
    """A call of `tool` for each of the ad's `scenes` scenes."""
    return [(tool, {"scene": scene, **arguments}) for scene in range(1, scenes + 1)]


def clip_calls(
    broll_scenes: tuple[int, ...], *, scenes: int = 3, quote: str = "Keep going"
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "start_scene_agent",
            {"scene": n, "instruction": "Film the planned scene.", "quotes": [quote]},
        )
        for n in range(1, scenes + 1)
    ]


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
    fake_model.respond("shorten_script", SHORTENED)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    chatting(
        product_page_url,
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 15})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Done: your script fits 15 seconds now."),
    )

    script_scene_agents(fake_model, scenes=3)
    chatting(
        "Looks good, go ahead",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(calls=clip_calls((2,), quote="Looks good, go ahead")),
        turn(says="The music is made, and every scene is on its way."),
    )
    chatting("Put it together", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!"))
    # Asking for an already assembled ad again reuses every paid scene.
    chatting(
        "Put it together again", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!")
    )


def test_the_b_roll_scene_plays_in_its_turn_with_the_voice_heard_over_it(
    ad_made_through_the_chat: None,
) -> None:
    ad = Job.objects.get().produced.get(kind="finished_ad")
    assert [(cut["scene"], cut["start"], cut["end"]) for cut in ad.cuts] == [
        (1, 0, 5),
        (2, 5, 10),
        (3, 10, 15),
    ]
    heard = read(ad.file)
    assert colour_at(heard, 7) == "lime"
    assert loudness(heard, "voice", between=(6, 7)) > -40


def test_the_scene_agent_gets_the_rewritten_action_after_shortening(
    ad_made_through_the_chat: None,
) -> None:
    shown = "\n".join(part.get("text", "") for part in handoffs("scene_agent")[2]["conversation"])
    assert SHORTENED_2 in shown
    assert "the mug turned slowly in a hand" in shown
    assert handoffs("write_first_send_prompt") == []
    assert sorted(
        Job.objects.get()
        .produced.filter(kind="starting_picture")
        .values_list("scene__number", flat=True)
    ) == [1, 2, 3]


def test_nothing_in_the_whole_flow_is_paid_for_twice(ad_made_through_the_chat: None) -> None:
    for purpose, count in {
        "draw_person": 1,
        "make_music": 1,
        "draw_scene_picture": 3,
        "make_broll_clip": 3,
        "transcribe_line": 3,
        "check_clip_motion": 3,
        "shorten_script": 1,
        "rewrite_line": 1,
    }.items():
        assert paid_for().count(purpose) == count
    assert not {"design_voice", "measure_voice", "speak_line", "make_talking_clip"} & set(
        paid_for()
    )


def test_the_shop_owner_is_never_told_which_scenes_are_b_roll(
    ad_made_through_the_chat: None, api: APIClient, session_id: str
) -> None:
    told = [text for role, text in chat(api, session_id) if role == "agent"]
    assert [text for text in told if "b-roll" in text.lower()] == []


# --- An ad with a B-roll scene that needs a photo of its own ------------------------------------

HANDLE = "Every mug is hand-thrown, so each one is a little different every day."

# What scene 3 needs that the mug's front can't show: its handle, which the page's photo 2,
# library item 2, shows.
HANDLE_PICTURES = [{"number": 2, "for": "in-use"}]

BOTH_WAYS = h3_plan(4)
BOTH_WAYS["plan"]["scenes"][2] = broll(
    {
        "line": HANDLE,
        "shows": "the mug's handle, seen from the side, as a hand lifts it",
        "scene_pictures": HANDLE_PICTURES,
    }
)


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
        f"Make an ad for {product_page_url}",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's your script. Shall I make it?"),
    )

    script_scene_agents(fake_model, scenes=4)
    chatting(
        "Looks good, go ahead",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(calls=clip_calls((2, 3), scenes=4, quote="Looks good, go ahead")),
        turn(says="Every scene is on its way."),
    )
    chatting("Put it together", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!"))


def test_every_broll_clip_is_sent_only_its_agents_checked_starting_picture(
    ad_made_with_needs: None,
) -> None:
    job = Job.objects.get()
    pictures = {
        p.scene.number: p.file
        for p in job.produced.filter(kind="starting_picture").select_related("scene")
        if p.scene is not None
    }
    assert [
        (h["starting_picture"], h["example_pictures"], h["seconds"])
        for h in handoffs("make_broll_clip")
    ] == [
        (pictures[n], [], job.produced.get(kind="clip", scene__number=n).seconds)
        for n in (1, 2, 3, 4)
    ]
    assert sorted(pictures) == [1, 2, 3, 4]
    for clip in job.produced.filter(kind="clip", scene__number__in=[2, 3]):
        sent = SentList.model_validate(clip.sent)
        assert sent.mode == "start_frame" and sent.pictures == []
        assert sent.starting_picture is not None
        assert clip.scene is not None
        assert sent.starting_picture.file == pictures[clip.scene.number]
        assert "real-time" in sent.prompt
        assert "Image 1" not in sent.prompt
    assert handoffs("write_first_send_prompt") == []


def test_each_scene_plays_whole_with_its_own_sound(ad_made_with_needs: None) -> None:
    ad = Job.objects.get().produced.get(kind="finished_ad")
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3, 4]
    clips = [Job.objects.get().produced.get(pk=cut["clip"]) for cut in ad.cuts]
    assert [cut["end"] - cut["start"] for cut in ad.cuts] == [clip.seconds for clip in clips]
    assert all(cut["clip_start"] == 0 for cut in ad.cuts)
    # Then the end card, its time counted in the ad's.
    assert ad.seconds == sum(clip.seconds or 0 for clip in clips) + END_CARD_SECONDS
    heard = read(ad.file)
    midpoints = [(cut["start"] + cut["end"]) / 2 for cut in ad.cuts]
    assert [colour_at(heard, at) for at in midpoints] == ["red", "lime", "blue", "yellow"]
    assert all(loudness(heard, "voice", between=(at, at + 0.5)) > -40 for at in midpoints)


def test_the_ad_with_a_scene_needing_a_photo_pays_for_each_thing_once(
    ad_made_with_needs: None,
) -> None:
    for purpose in (
        "make_broll_clip",
        "draw_scene_picture",
        "check_scene_picture",
        "transcribe_line",
        "check_clip_motion",
    ):
        assert paid_for().count(purpose) == 4
    assert paid_for().count("make_person_in_room") == 1
    assert paid_for().count("draw_person") == 1


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
    # Every scene exposes its checked starting picture and prompt.
    assert all(shown[n]["starting_picture"] for n in (1, 2, 3))
    clip = Job.objects.get().produced.get(kind="clip", scene__number=2)
    assert clip.sent is not None
    assert shown[2]["pictures"] == []
    assert clip.picture is not None
    assert shown[2]["starting_picture"]["file"] == clip.picture.file
    assert shown[2]["starting_picture"]["url"]
    assert shown[2]["prompt"] == handoffs("make_broll_clip")[1]["prompt"]


# --- Spec #247: the first message, the plan first, the fold, nothing of the old path ----------


def first_ad_from_one_link(producer: ScriptedProducer, fake_model: FakeModel, link: str) -> None:
    from .test_h3_switch import first_h3_ad

    first_h3_ad(producer, fake_model, link)


# Media generation purposes that must wait when the owner asks to see a plan first.
MAKES = {
    "draw_person",
    "make_broll_clip",
    "draw_scene_picture",
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
    # The ad ends on its end card, from the plan and the product's link, after its last scene.
    last = ad.cuts[-1]["end"]
    assert ad.end_card == {
        "name": "Stoneware Mug",
        "line": "Hand-thrown, holds 350 ml, dishwasher safe.",
        "price": "$24.00",
        "website": website_of(product_page_url),
        "start": last,
        "end": last + END_CARD_SECONDS,
        "file": job.photos.get(position=1).file,
    }
    assert ad.seconds == last + END_CARD_SECONDS
    assert video(read(ad.file))[2] == pytest.approx(ad.seconds, abs=0.1)
    assert shows_the_end_card(read(ad.file), last + END_CARD_SECONDS / 2)
    # Every B-roll clip is judged: scene 2's, once.
    assert paid_for().count("check_clip_motion") == 3
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
    # No person, scene picture or clip is made, and no scene work starts.
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
        clip_calls((2,), quote="Looks good, make it"),
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


def test_one_product_link_starts_all_scene_agents_together_and_finishes_with_a_talking_scene(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    from jobs.models import SceneStep

    plan = h3_plan()
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    script_scene_agents(fake_model, scenes=3)
    producer.told(
        product_page_url,
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "light upbeat lo-fi"}),
        clip_calls((2,), quote=product_page_url),
        "Making your ad.",
    )
    pending = SceneStep.objects.filter(pk__in=producer.steps.held, kind="scene_agent")
    assert sorted(pending.values_list("scene__number", flat=True)) == [1, 2, 3]
    producer.finished_meanwhile()
    producer.finished_meanwhile()
    producer.woken(("assemble_ad", {}), "Here is your ad.")
    job = Job.objects.get()
    assert job.produced.filter(kind="finished_ad").exists(), results_of("assemble_ad")
    assert job.produced.filter(kind="clip").count() == 3
    assert paid_for().count("make_broll_clip") == 3
    assert paid_for().count("make_person_in_room") == 1
    assert paid_for().count("check_clip_motion") == 3
    assert not ToolCall.objects.filter(tool="ask_owner").exists()
