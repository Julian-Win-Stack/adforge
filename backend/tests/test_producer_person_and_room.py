"""The producer remakes the presenter and sets each scene's place itself (spec #247 points 5
and 9; ticket #256): "a man" remakes the person's look and voice with its instruction on
top of the standard prompt; "show her in five different rooms" gives five scenes a place of
their own, each with the presenter drawn in it. Code marks what the change made stale and
says so in the tool's answer; nothing is made again until the producer calls the scene
tools. The picture of the presenter in a place is of that place and of the person as they
stand, never the ad's newest. Driven through the chat; the producer is scripted and every
model faked at the gateway; the clips are real tiny videos."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models import Sum

from agents.models import ToolCall
from agents.money import dollars
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import stale
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.work import scene_place

from .conftest import NO_CHOICES, PLAN, HeldSteps, broll, facts_ok, handoffs, paid_for
from .test_producer_broll import (
    TALKING_CHOICE,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_fixes import shown  # noqa: F401 (a fixture)
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]


def answer_of(tool: str) -> str:
    """What the newest call of `tool` handed back."""
    call = ToolCall.objects.filter(tool=tool).last()
    assert call is not None, f"{tool} was called"
    return call.result


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, then replies."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="Done."))
    say(text)


def stale_by_scene() -> list[list[SceneStep.Kind]]:
    return [stale.stale_steps(scene) for scene in Scene.objects.order_by("number")]


# --- "A man": the person made again ------------------------------------------------------------

LOOKS = "A carpenter in his forties with a short grey beard, in a denim shirt."
VOICE = "A calm, low man's voice in his forties with a slight northern accent."
ASKED = "The shop owner wants a man; keep him friendly and plainly dressed."


def test_a_man_remakes_the_look_and_voice_with_the_instruction_and_marks_what_shows_them_stale(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    paid_before = len(paid_for())
    owner_says(
        fake_model,
        say,
        "Make it a man.",
        (
            "remake_person",
            {
                "remake": "both",
                "gender": "man",
                "looks": LOOKS,
                "voice": VOICE,
                "instruction": ASKED,
            },
        ),
    )

    # A new portrait and a new voice, measured on the script; nothing else made.
    assert paid_for()[paid_before:] == ["draw_person", "design_voice", "measure_voice"]
    job = Job.objects.get()
    assert job.person_gender == "man"
    portrait = job.produced.filter(kind="portrait").order_by("version").last()
    voice = job.produced.filter(kind="voice").order_by("version").last()
    assert portrait is not None and voice is not None
    assert (portrait.version, voice.version) == (2, 2)
    assert voice.words_per_second is not None
    # The standard prompt, made from the plan's person as it now stands, with the producer's
    # instruction on top.
    drawn = handoffs("draw_person")[-1]["prompt"]
    assert f"The person: a man. {LOOKS}" in drawn
    assert drawn.endswith(ASKED)
    designed = handoffs("design_voice")[-1]["description"]
    assert designed.startswith(f"A man's voice. {VOICE}") and designed.endswith(ASKED)
    # Shown to the shop owner.
    assert Attachment.objects.filter(file=portrait.file, kind="picture").exists()
    assert Attachment.objects.filter(file=voice.file, kind="sound").exists()

    # The talking scenes show the person: their pictures and every scene's audio are stale;
    # scene 2 shows the mug with nobody's face, so its picture stands.
    every = ["starting_picture", "line_audio", "transcript", "clip"]
    assert stale_by_scene() == [every, ["line_audio", "transcript", "clip"], every]
    answer = answer_of("remake_person")
    assert "Scene 1's starting picture, audio, transcript and clip are stale" in answer
    assert "Scene 2's audio, transcript and clip are stale" in answer
    assert "Scene 3's starting picture, audio, transcript and clip are stale" in answer
    assert "It cost $" in answer
    assert not job.scenes.filter(status="finished").exists()
    assert steps.held == []

    # The producer checks the new portrait through the look.
    fake_model.respond(
        "look",
        {"sees": "A bearded man.", "wrong": "Nothing", "root_cause": "", "direction": ""},
    )
    owner_says(
        fake_model,
        say,
        "Go on",
        ("look", {"scene": None, "what": "person", "number": None, "question": "Is he a man?"}),
    )
    (looked,) = handoffs("look")
    assert looked["shown"] == "The presenter's portrait, version 2."
    assert "Sees: A bearded man." in answer_of("look")

    # A starting picture made again is drawn from the new portrait.
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    owner_says(fake_model, say, "Go on", ("make_starting_picture", {"scene": 1, "note": None}))
    run(fake_model, steps)
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == portrait.file
    assert stale.stale_steps(Scene.objects.get(number=1))[0] != "starting_picture"


def test_a_new_look_alone_keeps_the_voice_and_every_scenes_audio(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    paid_before = len(paid_for())
    owner_says(
        fake_model,
        say,
        "Give her short hair.",
        ("remake_person", {"remake": "look", "instruction": "Short hair."}),
    )

    assert paid_for()[paid_before:] == ["draw_person"]
    every = ["starting_picture", "clip"]
    assert stale_by_scene() == [every, [], every]


def test_a_new_gender_with_only_the_look_remade_is_refused_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    paid_before = len(paid_for())
    owner_says(
        fake_model, say, "Make it a man.", ("remake_person", {"remake": "look", "gender": "man"})
    )

    answer = answer_of("remake_person")
    assert answer.startswith("Refused:") and '"both"' in answer
    assert paid_for()[paid_before:] == []
    assert Job.objects.get().person_gender == "woman"


def test_a_remake_that_fails_leaves_the_plans_person_as_the_ad_shows_them(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    fake_model.respond("draw_person", ConnectionError("picture service down"))
    owner_says(
        fake_model, say, "Make her older.", ("remake_person", {"remake": "look", "looks": LOOKS})
    )

    assert answer_of("remake_person").startswith("Failed:")
    assert Job.objects.get().person_looks != LOOKS


def test_new_looks_with_only_the_voice_remade_are_refused_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    paid_before = len(paid_for())
    owner_says(
        fake_model, say, "Make it a man.", ("remake_person", {"remake": "voice", "looks": LOOKS})
    )

    assert answer_of("remake_person").startswith("Refused:")
    assert paid_for()[paid_before:] == []
    assert Job.objects.get().person_looks != LOOKS
    assert stale_by_scene() == [[], [], []]


# --- "Show her in five different rooms" -------------------------------------------------------

FACE: dict[str, Any] = {"person_shown": "has face"}
ROOMS_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            {"line": "Meet the Stoneware Mug from Kiln & Co.", "overlay": "Kiln & Co"},
            broll(
                {
                    **FACE,
                    "line": "Hand-thrown, holds 350 ml, and dishwasher safe too.",
                    "shows": "She lifts the mug to her lips and takes a sip.",
                }
            ),
            broll(
                {
                    **FACE,
                    "line": "Every mug is hand-thrown, so each one is its own.",
                    "shows": "She wraps both hands around the mug and smiles.",
                }
            ),
            broll(
                {
                    **FACE,
                    "line": "It holds 350 ml of your favourite hot drink.",
                    "shows": "She sets the mug down on a wooden table.",
                }
            ),
            broll(
                {
                    **FACE,
                    "line": "It is dishwasher safe, so cleaning up takes seconds.",
                    "shows": "She places the mug on the top rack of a dishwasher.",
                }
            ),
            broll(
                {
                    **FACE,
                    "line": "Yours today for just $24.00, from Kiln & Co.",
                    "shows": "She holds the mug out towards the camera.",
                    "overlay": "$24.00",
                }
            ),
        ],
    },
}
SCENES = (1, 2, 3, 4, 5, 6)
ROOMS = (2, 3, 4, 5, 6)
PLACES = {
    2: "A bright kitchen with white tiles and a window over the sink.",
    3: "A cosy living room with a grey sofa and a lamp.",
    4: "A small balcony with plants and a city view.",
    5: "A modern kitchen with dark wood cabinets.",
    6: "A café counter with a coffee machine behind it.",
}


def make_scene_clips(
    fake_model: FakeModel, steps: HeldSteps, say: Say, numbers: tuple[int, ...]
) -> None:
    """Use the producer's live doors, then let the real scene tools finish."""
    from .scene_script import script_scene_agents

    scenes = [Scene.objects.get(number=number) for number in numbers]
    broll_scenes = [scene for scene in scenes if scene.shows]
    script_scene_agents(fake_model, scenes=len(broll_scenes))
    calls: list[tuple[str, dict[str, Any]]] = []
    for scene in scenes:
        if not scene.shows:
            calls.append(("make_clip", {"scene": scene.number}))
        elif scene.produced.filter(kind="clip").exists():
            calls.append(
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": scene.number,
                                "instruction": "Use this scene's current person, place and action.",
                                "quotes": ["Go on"],
                            }
                        ]
                    },
                )
            )
        else:
            calls.append(
                (
                    "start_scene_agent",
                    {
                        "scene": scene.number,
                        "instruction": "Show this scene's planned action.",
                        "quotes": ["Go on"],
                    },
                )
            )
    owner_says(fake_model, say, "Go on", *calls)
    run(fake_model, steps)


@pytest.fixture
def five_shown(fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Say) -> ProducedItem:
    """A chat whose finished ad is shown: scene 1 has the person talking, scenes 2 to 6 show
    her face with the mug, all in the ad's place."""
    fake_model.respond("plan_ad", ROOMS_PLAN)
    owner_says(fake_model, say, "Plan it", ("plan_ad", {}))
    fake_model.respond("fact_check", facts_ok(*SCENES))
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(says="Checked."),
    )
    say("Go on")
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    owner_says(
        fake_model,
        say,
        "Go on",
        ("make_starting_picture", {"scene": 1, "note": None}),
        *[("make_line_audio", {"scene": n}) for n in SCENES],
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", *[("transcribe_line_audio", {"scene": n}) for n in SCENES])
    run(fake_model, steps)
    make_scene_clips(fake_model, steps, say, SCENES)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def test_five_different_rooms_gives_each_scene_its_own_place_and_new_clips_make_a_new_ad(
    fake_model: FakeModel, five_shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    job = Job.objects.get()
    ad_person = job.produced.get(kind="person_in_room")
    paid_before = len(paid_for())
    owner_says(
        fake_model,
        say,
        "Show her in five different rooms.",
        *[("set_place", {"scene": n, "new_place": PLACES[n]}) for n in ROOMS],
    )

    # A room drawn fresh for each scene, and the presenter, from her portrait, in each.
    assert paid_for()[paid_before:] == ["draw_scene_room", "make_scene_person_in_room"] * 5
    portrait = job.produced.get(kind="portrait")
    for number in ROOMS:
        scene = Scene.objects.get(number=number)
        room = scene.produced.get(kind="scene_room")
        person = scene.produced.get(kind="scene_person_in_room")
        assert PLACES[number] in str(room.text)
        assert (person.made_from, person.person) == (room, portrait)
        assert scene.place == person
        # What its clips are sent of its place: its own, not the ad's.
        assert scene_place(scene, scene.person_shown) == ("person_in_room", person)
    assert [handoff["pictures"] for handoff in handoffs("make_scene_person_in_room")] == [
        [portrait.file, Scene.objects.get(number=n).produced.get(kind="scene_room").file]
        for n in ROOMS
    ]
    # The talking scene keeps the ad's place, and no scene was sent the ad's picture.
    assert scene_place(Scene.objects.get(number=1), "has face")[1] == ad_person
    # Every B-roll starting picture and its clip are stale; nothing was started.
    assert stale_by_scene() == [[], *[["starting_picture", "clip"]] * 5]
    for call in ToolCall.objects.filter(tool="set_place"):
        assert "starting picture and clip are stale now" in call.result
        assert "Look at it" in call.result
    assert steps.held == []
    # Each picture was shown to the shop owner.
    assert (
        Attachment.objects.filter(
            file__in=job.produced.filter(kind="scene_person_in_room").values("file")
        ).count()
        == 5
    )

    # The scripted producer makes the five clips again and assembles.
    make_scene_clips(fake_model, steps, say, ROOMS)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    assert paid_for()[paid_before:].count("make_broll_clip") == 5
    assert "make_talking_clip" not in paid_for()[paid_before:]
    # Each starting picture used its own person-and-room input; H3 received only that start.
    for number in ROOMS:
        scene = Scene.objects.get(number=number)
        clip = scene.produced.filter(kind="clip").order_by("version").last()
        assert clip is not None and clip.sent is not None
        assert clip.picture is not None and clip.picture.sent is not None
        shared = [
            photo["file"]
            for photo in clip.picture.sent["photos"]
            if photo.get("library_number") is None
        ]
        assert shared == [scene.produced.get(kind="scene_person_in_room").file]
        assert clip.sent["pictures"] == []
    ad = job.produced.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None and ad.version == 2
    assert stale_by_scene() == [[]] * 6
    assert ad.cuts[0]["clip"] == five_shown.cuts[0]["clip"]
    spent = ModelCall.objects.filter(
        created_at__gt=five_shown.created_at, created_at__lt=ad.created_at
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    assert f"The changes since ad version 1 cost {dollars(spent)}" in answer_of("assemble_ad")


def test_a_small_edit_of_a_scenes_place_edits_the_picture_it_has_now(
    fake_model: FakeModel, five_shown: ProducedItem, say: Say
) -> None:
    job = Job.objects.get()
    ad_person = job.produced.get(kind="person_in_room")
    paid_before = len(paid_for())
    owner_says(
        fake_model,
        say,
        "Make scene 3's kitchen brighter.",
        ("set_place", {"scene": 3, "edit": "Make the light brighter."}),
    )

    assert paid_for()[paid_before:] == ["edit_scene_place"]
    scene = Scene.objects.get(number=3)
    edited = scene.produced.get(kind="scene_person_in_room")
    assert handoffs("edit_scene_place")[-1]["pictures"] == [ad_person.file]
    assert (scene.place, edited.made_from, edited.person) == (
        edited,
        ad_person,
        job.produced.get(kind="portrait"),
    )
    assert stale_by_scene() == [[], [], ["starting_picture", "clip"], [], [], []]
    assert "Scene 3's starting picture and clip are stale now" in answer_of("set_place")


def test_a_scene_edited_in_the_ads_place_that_shows_nobody_is_sent_the_ads_room(
    fake_model: FakeModel, five_shown: ProducedItem, say: Say
) -> None:
    owner_says(
        fake_model,
        say,
        "Brighter.",
        ("set_place", {"scene": 3, "edit": "Make the light brighter."}),
    )
    owner_says(
        fake_model, say, "Hide her face.", ("edit_scene", {"scene": 3, "person_shown": "no face"})
    )

    scene = Scene.objects.get(number=3)
    # Its own place is the ad's presenter picture edited, so its room is the ad's room alone.
    assert scene_place(scene, scene.person_shown) == (
        "room",
        Job.objects.get().produced.get(kind="room_alone"),
    )


def test_a_place_for_a_talking_scene_is_refused_with_what_to_do_instead(
    fake_model: FakeModel, five_shown: ProducedItem, say: Say
) -> None:
    paid_before = len(paid_for())
    owner_says(
        fake_model,
        say,
        "Put her in a garden.",
        ("set_place", {"scene": 1, "new_place": "A garden."}),
    )

    answer = answer_of("set_place")
    assert answer.startswith("Refused:") and "edit_scene" in answer
    assert paid_for()[paid_before:] == []
    assert Scene.objects.get(number=1).place is None


def test_a_new_person_is_drawn_again_in_each_place_never_the_ads_newest(
    fake_model: FakeModel, five_shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(
        fake_model, say, "Garden for scene 2.", ("set_place", {"scene": 2, "new_place": PLACES[2]})
    )
    job = Job.objects.get()
    own_room = Scene.objects.get(number=2).produced.get(kind="scene_room")
    owner_says(fake_model, say, "Make her younger.", ("remake_person", {"remake": "look"}))
    new = job.produced.filter(kind="portrait").order_by("version").last()
    assert new is not None and new.version == 2
    # The has-face scenes show her as she was: the talking scene's picture and clip, and the
    # starting pictures and clips of all scenes showing her face.
    assert stale_by_scene() == [["starting_picture", "clip"]] * 6
    assert "drawn again for the new person" in answer_of("remake_person")
    # Until drawn again, no picture of her in a place is sent: not the old person's.
    for number in (2, 3):
        scene = Scene.objects.get(number=number)
        assert scene_place(scene, scene.person_shown)[1] is None

    paid_before = len(paid_for())
    make_scene_clips(fake_model, steps, say, (2, 3))

    # Drawn again once for the ad's place and once in scene 2's own room, from the new
    # portrait; each kept with the portrait it shows.
    assert [p for p in paid_for()[paid_before:] if "person_in_room" in p] == [
        "make_person_in_room",
        "make_scene_person_in_room",
    ]
    two, three = Scene.objects.get(number=2), Scene.objects.get(number=3)
    own = scene_place(two, two.person_shown)[1]
    ad = scene_place(three, three.person_shown)[1]
    assert own is not None and ad is not None
    assert (own.kind, own.person, own.made_from) == ("scene_person_in_room", new, own_room)
    assert (ad.kind, ad.person) == ("person_in_room", new)
    assert handoffs("make_scene_person_in_room")[-1]["pictures"] == [new.file, own_room.file]


def test_a_picture_edit_keeps_its_shared_person_as_an_input_that_can_go_stale(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    from .test_producer_broll import SHOWS
    from .test_scene_agent_changes import change
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.respond(
        "check_scene_picture",
        {**PICTURE_PASSES, "physics": {"passes": False, "says": "One thumb floats."}},
        PICTURE_PASSES,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(shows=SHOWS, person_shown="has face")]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(
            calls=[
                (
                    "edit_starting_picture",
                    {
                        "keep": ["the person", "the room"],
                        "change": "Join the thumb to the hand",
                        "anything_else": "not needed here",
                        "picks": PICTURE_FORM["picks"],
                    },
                )
            ]
        ),
        turn(calls=[("film", FILM)]),
    )
    owner_says(
        fake_model,
        say,
        "Go on",
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show the presenter using it.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    assert stale.stale_steps(Scene.objects.get(number=2)) == []
    owner_says(fake_model, say, "Make her younger.", ("remake_person", {"remake": "look"}))
    assert stale.stale_steps(Scene.objects.get(number=2)) == ["starting_picture", "clip"]
