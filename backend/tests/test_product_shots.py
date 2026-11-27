"""Product-shots ads (spec #767): silent scenes of the product with no presenter and no spoken
line, planned by the producer itself. Driven through the chat, with the producer and the
scene agents scripted and every outside service faked."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.file_store import read
from agents.models import ToolCall
from agents.scene_forms import SILENT_PICTURE_OPENING, SilentVideoForm, silent_video_prompt
from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.types import ClipFailed
from jobs import assembly, product_shots
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.planning import END_CARD_SECONDS

from .conftest import (
    NO_CHOICES,
    READABLE,
    ScriptedProducer,
    facts_ok,
    handoffs,
    paid_for,
    recorded,
    results_of,
    video,
)
from .test_h3_planning import h3_plan
from .test_scene_pictures import PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)

THREE_SCENES: list[dict[str, Any]] = [
    {
        "shows": "The mug alone on pale stone, the light crossing its glaze.",
        "photo": 1,
        "move": "push in",
        "overlay": "Hand-thrown",
        "seconds": 4,
    },
    {
        "shows": "The mug from the side, its handle in soft window light.",
        "photo": 2,
        "move": "pull back",
        "overlay": "Holds 350 ml",
        "seconds": 5,
    },
    {
        "shows": "The mug on a breakfast table, steam rising.",
        "photo": 1,
        "move": "push in",
        "overlay": "",
        "seconds": 4,
    },
]


# The producer's choose_ad_style arguments for a product-shots ad.
CHOSEN = {
    "doubts": ["Is it beautiful enough to want?", "Will it last?"],
    "chosen_doubt": "Is it beautiful enough to want?",
    "style": "product shots",
    "reason": "Your mug sells on its looks, so it's shown at its best.",
    "owner_asked": True,
}


def plan(scenes: list[dict[str, Any]] = THREE_SCENES, **changes: Any) -> dict[str, Any]:
    """The producer's plan_product_shots arguments."""
    return {
        "story": "A hand-thrown mug, made one at a time, at its best in morning light.",
        "built_on": "Each mug is thrown by hand, so no two are the same.",
        "scenes": scenes,
        "music_mood": "calm warm piano",
        "product_name": "Stoneware Mug",
        "product_colour": "white",
        "end_card_line": "Hand-thrown, holds 350 ml, dishwasher safe.",
        "end_card_price": "$24.00",
        **changes,
    }


SILENT_PICTURE = {
    "picks": [{"item": 1, "take": "the mug's exact shape, glaze and handle"}],
    "form": {
        "setting": "on smooth pale stone beside a sunlit window",
        "product": "the whole mug, large in the middle of the frame, handle to the right",
        "light": "soft morning light from the left",
        "framing": "close-up, the mug fills the middle",
        "anything_else": "not needed here",
    },
}
SILENT_FILM: dict[str, Any] = {
    "move": "push in",
    "crop_x": 50,
    "crop_y": 50,
    "form": {
        "frame_shows": "A white mug on pale stone in morning light.",
        "product": "a white glazed stoneware mug with a round handle",
        "light": "a slow soft sweep of light across the glaze",
        "lock_line": "The mug keeps its exact shape, colour and details.",
    },
}


def script_silent_scenes(fake: FakeModel, count: int) -> None:
    """Each silent scene's agent draws an AI-made picture, which passes, and films it once."""
    fake.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    for _ in range(count):
        fake.respond(
            "scene_agent",
            turn(calls=[("draw_silent_picture", SILENT_PICTURE)]),
            turn(calls=[("film_silent", SILENT_FILM)]),
        )


def planned_product_shots(
    producer: ScriptedProducer, fake: FakeModel, link: str, scenes: list[dict[str, Any]]
) -> None:
    fake.respond("check_page", READABLE)
    producer.told(
        f"Make a product-shots ad for {link}",
        ("read_page", {"link": link, "target_seconds": None}),
        ("choose_ad_style", CHOSEN),
        ("plan_product_shots", plan(scenes)),
        "Here's the plan.",
    )


def filmed_product_shots(producer: ScriptedProducer, fake: FakeModel, link: str) -> None:
    """A product-shots ad from one link: planned, checked, its music made and its three
    silent scenes filmed."""
    fake.respond("check_page", READABLE)
    fake.respond("fact_check", facts_ok(1, 2, 3))
    script_silent_scenes(fake, 3)
    producer.told(
        f"Make a product-shots ad for {link}",
        ("read_page", {"link": link, "target_seconds": None}),
        ("choose_ad_style", CHOSEN),
        ("plan_product_shots", plan()),
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "calm warm piano"}),
        [
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Film the planned shot.", "quotes": [link]},
            )
            for n in (1, 2, 3)
        ],
        "I'm making your ad.",
    )
    producer.finished_meanwhile()
    producer.finished_meanwhile()
    producer.woken("Every scene is filmed.")


def test_a_product_shots_ad_is_planned_by_the_producer_and_filmed_with_no_person_and_no_line(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)

    job = Job.objects.get()
    assert job.ad_style == "product_shots"
    assert job.shots_plan["built_on"] == "Each mug is thrown by hand, so no two are the same."
    scenes = list(job.scenes.order_by("number"))
    assert [scene.kind for scene in scenes] == [Scene.Kind.SILENT] * 3
    assert [scene.line for scene in scenes] == ["", "", ""]
    assert [scene.overlay for scene in scenes] == ["Hand-thrown", "Holds 350 ml", ""]
    assert [(scene.silent or {})["seconds"] for scene in scenes] == [4, 5, 4]
    # No person: no portrait, no voice, no room.
    assert not {"draw_person", "make_person_in_room", "make_room_alone"} & set(paid_for())
    assert not job.produced.filter(kind=ProducedItem.Kind.PORTRAIT).exists()
    # Every scene reached a passed clip, judged by the clip judge, never transcribed.
    assert [scene.status for scene in scenes] == [Scene.Status.FINISHED] * 3
    assert paid_for().count("make_broll_clip") == 3
    assert paid_for().count("check_clip_pass_through") == 3
    assert "transcribe_line" not in paid_for()
    # Each clip is filmed from the silent video form: no presenter and no line.
    filmed = silent_video_prompt(SilentVideoForm(**SILENT_FILM["form"]), "push in")
    assert [sent["prompt"] for sent in handoffs("make_broll_clip")] == [filmed] * 3
    # Each starting picture is drawn by the B-roll path from the photos alone, no shared place.
    for drawn in handoffs("draw_scene_picture"):
        assert drawn["prompt"].startswith(SILENT_PICTURE_OPENING)
    # Each clip is filmed between its starting picture and a crop of it: both ends pinned.
    for clip in job.produced.filter(kind=ProducedItem.Kind.CLIP):
        assert clip.sent is not None and clip.sent["end_picture"] is not None
    # The music was made for this style: instrumental, as long as the ad with room to spare.
    (music,) = handoffs("make_music")
    assert music["prompt"].endswith(product_shots.MUSIC_FOR_THE_STYLE)
    assert music["seconds"] >= 4 + 5 + 4 + 2


def test_no_person_is_created_for_a_product_shots_ad(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)

    producer.told("Go on", ("create_person", {}), "No presenter in this ad.")

    assert results_of("create_person")[-1].startswith("Refused")
    assert "draw_person" not in paid_for()


def test_an_overlay_the_page_does_not_state_is_caught_and_nothing_is_filmed(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)
    wrong = facts_ok(1, 2, 3)
    wrong["lines"][1] = {
        "scene": 2,
        "verdict": "wrong",
        "wrong": "line",
        "problem": "The page doesn't say how much it holds in ounces.",
        "page_says": "Holds 350 ml.",
    }
    fake_model.respond("fact_check", wrong)

    producer.told(
        "Go on",
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "calm warm piano"}),
        "I'll fix scene 2's overlay.",
    )

    # The overlay was checked against the page, not a spoken line.
    (checked,) = handoffs("fact_check")
    assert [line["overlay"] for line in checked["lines"]] == [
        "Hand-thrown",
        "Holds 350 ml",
        None,
    ]
    assert "Scene 2" in results_of("run_planning_checks")[-1]
    assert results_of("create_music")[-1].startswith("Refused")
    assert any("Scene 2" in text for _, text in recorded(api, session_id))


def test_a_longer_ad_with_more_scenes_is_planned_and_a_new_plan_replaces_the_old(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)
    five = [{**THREE_SCENES[n % 3], "seconds": 5.5} for n in range(5)]

    producer.told(
        "Make it 30 seconds with 5 scenes",
        ("plan_product_shots", plan(five)),
        "Here's the longer plan.",
    )

    job = Job.objects.get()
    assert job.scenes.count() == 5
    assert product_shots.ad_seconds(job) == 29.5  # 5 x 5.5 s and the 2 s end card
    assert job.target_seconds is None  # the owner's own target is kept
    assert Scene.with_removed.filter(job=job, removed_at__isnull=False).count() == 3


def test_a_plan_naming_a_photo_the_library_does_not_hold_is_refused(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    fake_model.respond("check_page", READABLE)
    bad = [{**THREE_SCENES[0], "photo": 9}]

    producer.told(
        f"Make a product-shots ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("choose_ad_style", CHOSEN),
        ("plan_product_shots", plan(bad)),
        "I'll fix the plan.",
    )

    assert results_of("plan_product_shots")[-1].startswith("Refused")
    assert not Job.objects.get().scenes.exists()


def test_a_note_on_a_silent_scene_opens_another_run_of_its_agent(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)
    script_silent_scenes(fake_model, 1)

    producer.told(
        "Scene 2 should be warmer",
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": "Make the light warmer.",
                        "quotes": ["should be warmer"],
                        "owner_asked": True,
                        "new_asked_change": "Scene 2's light is warmer.",
                    }
                ]
            },
        ),
        "I'll make scene 2 warmer.",
    )
    producer.steps_run("Scene 2 is warmer now.")

    scene = Job.objects.get().scenes.get(number=2)
    assert scene.produced.filter(kind=ProducedItem.Kind.CLIP).count() == 2
    assert paid_for().count("make_broll_clip") == 4


def test_a_link_with_no_style_asked_still_plans_a_person_talking_ad(
    fake_model: FakeModel, planned: None
) -> None:
    job = Job.objects.get()
    assert job.ad_style == "person_talking"
    assert {scene.kind for scene in job.scenes.all()} <= {Scene.Kind.TALKING, Scene.Kind.BROLL}
    assert ToolCall.objects.filter(tool="plan_ad").exists()


def test_an_overlay_is_changed_on_a_filmed_ad_without_filming_again(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)

    producer.told(
        "Make scene 1 say Made by hand",
        ("edit_scene", {"scene": 1, "overlay": "Made by hand", "owner_asked": True}),
        "Changed.",
    )

    scene = Job.objects.get().scenes.get(number=1)
    assert scene.overlay == "Made by hand"
    assert not scene.fact_checked
    assert "nothing is filmed again" in results_of("edit_scene")[-1]
    assert paid_for().count("make_broll_clip") == 3


def test_a_silent_scene_keeps_its_plan_when_asked_for_a_spoken_line(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)

    producer.told(
        "Add a line",
        ("edit_scene", {"scene": 1, "line": "Look at this mug.", "owner_asked": True}),
        ("add_scene", {"at": 4, "line": "Buy it now.", "kind": "broll", "owner_asked": True}),
        "I'll plan it again.",
    )

    assert results_of("edit_scene")[-1].startswith("Refused")
    assert results_of("add_scene")[-1].startswith("Refused")
    job = Job.objects.get()
    assert [scene.kind for scene in job.scenes.all()] == [Scene.Kind.SILENT] * 3
    assert job.scenes.get(number=1).line == ""


def test_the_ad_is_not_planned_again_while_a_scene_is_being_made(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)
    scene = Job.objects.get().scenes.get(number=2)
    SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.SCENE_AGENT,
        status=SceneStep.Status.RUNNING,
        tool_call=ToolCall.objects.get(tool="plan_product_shots"),
    )

    producer.told(
        "Make it 30 seconds",
        ("plan_product_shots", plan([{**THREE_SCENES[0], "seconds": 7}] * 4)),
        ("plan_ad", {}),
        "I'll plan it again once scene 2 is ready.",
    )

    assert "scene 2 is still being made" in results_of("plan_product_shots")[-1]
    assert results_of("plan_ad")[-1].startswith("Refused")
    job = Job.objects.get()
    assert job.scenes.count() == 3
    assert job.ad_style == "product_shots"


def test_silent_scenes_wait_for_music_made_for_this_plan(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    # Music from an earlier plan, with a voice over it in mind and too short for this one.
    ProducedItem.objects.create(
        job=Job.objects.get(),
        kind=ProducedItem.Kind.MUSIC,
        file="old-music.mp3",
        text="calm piano for a voice-over",
        seconds=8,
        version=1,
    )

    producer.told(
        "Go on",
        ("run_planning_checks", NO_CHOICES),
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned shot.", "quotes": ["Go on"]},
        ),
        "I'll make the music first.",
    )

    assert "create_music" in results_of("start_scene_agent")[-1]
    assert not ToolCall.objects.filter(tool="draw_silent_picture").exists()


def test_a_clip_the_video_model_refuses_is_told_to_the_developer_record(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("fact_check", facts_ok(1))
    fake_model.respond("make_clip", ClipFailed("H3 rejected the picture pair"))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_silent_picture", SILENT_PICTURE)]),
        turn(calls=[("film_silent", SILENT_FILM)]),
        turn(calls=[("give_up", {"reason": "The video model refused the clip."})]),
    )

    producer.told(
        f"Make a product-shots ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("choose_ad_style", CHOSEN),
        ("plan_product_shots", plan(THREE_SCENES[:1])),
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "calm warm piano"}),
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned shot.", "quotes": [product_page_url]},
        ),
        "I'm making your ad.",
    )
    producer.woken("Scene 1 couldn't be filmed.")

    assert "H3 rejected the picture pair" in results_of("film_silent")[-1]
    assert any(
        "refused scene 1's clip" in text and "H3 rejected the picture pair" in text
        for _, text in recorded(api, session_id)
    )


def test_the_style_is_chosen_from_the_buyers_doubt_and_kept_for_the_developer(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    fake_model.respond("check_page", READABLE)
    chosen = {**CHOSEN, "owner_asked": False}

    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("choose_ad_style", chosen),
        "Product shots: your mug sells on its looks.",
    )

    job = Job.objects.get()
    assert job.ad_style == "product_shots"
    assert job.style_choice == {
        "doubts": chosen["doubts"],
        "chosen": chosen["chosen_doubt"],
        "style": "product_shots",
        "why": chosen["reason"],
        "owner_asked": False,
    }
    told = results_of("choose_ad_style")[-1]
    assert '"Product shots"' in told and "plan_product_shots" in told


def test_a_doubt_not_among_those_named_is_refused(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    fake_model.respond("check_page", READABLE)

    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("choose_ad_style", {**CHOSEN, "chosen_doubt": "Is it cheap?"}),
        "Let me choose again.",
    )

    assert results_of("choose_ad_style")[-1].startswith("Refused")
    assert Job.objects.get().style_choice == {}


def test_asking_for_a_person_talking_instead_switches_the_style_and_plans_again(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    planned_product_shots(producer, fake_model, product_page_url, THREE_SCENES)

    producer.told(
        "Make it a person talking instead",
        (
            "choose_ad_style",
            {**CHOSEN, "style": "person talking", "reason": "You asked for a person."},
        ),
        "Person talking, as you asked.",
    )

    job = Job.objects.get()
    assert job.ad_style == "person_talking"
    assert job.shots_plan == {}
    assert job.target_seconds is None
    assert "earlier plan was taken out" in results_of("choose_ad_style")[-1]
    assert "plan_ad" in results_of("choose_ad_style")[-1]

    fake_model.respond("plan_ad", h3_plan())
    producer.told("Go on", ("plan_ad", {}), "Here's the person-talking plan.")

    job = Job.objects.get()
    assert [scene.kind for scene in job.scenes.order_by("number")] == [
        each["kind"] for each in h3_plan()["plan"]["scenes"]
    ]
    assert job.target_seconds is None


def test_a_chat_pinned_to_person_talking_never_makes_product_shots(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str, session_id: str
) -> None:
    Session.objects.filter(pk=session_id).update(pinned_ad_style="person_talking")
    fake_model.respond("check_page", READABLE)

    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("choose_ad_style", {**CHOSEN, "owner_asked": False}),
        ("plan_product_shots", plan()),
        "I'll plan a person talking.",
    )

    assert "Person talking" in results_of("choose_ad_style")[-1]
    assert results_of("plan_product_shots")[-1].startswith("Refused")
    assert Job.objects.get().ad_style == "person_talking"


def test_a_filmed_product_shots_ad_is_put_together_with_its_overlays_and_the_music_alone(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)

    def clips_are_not_listened_to(media: object) -> bool:
        raise AssertionError("a product-shots ad plays the music only")

    monkeypatch.setattr(assembly, "has_sound", clips_are_not_listened_to)
    volumes = []
    fit_the_music = assembly.music_filters

    def music_filters(music_seconds: float, ad_seconds: float, volume: float) -> str:
        volumes.append(volume)
        return fit_the_music(music_seconds, ad_seconds, volume)

    monkeypatch.setattr(assembly, "music_filters", music_filters)
    producer.told("Put it together", ("assemble_ad", {}), "Here's your ad!")

    (ad,) = Job.objects.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD)
    # Each clip cut to its planned length, with its overlay, then the end card.
    assert [(cut["start"], cut["end"], cut["overlay"]) for cut in ad.cuts] == [
        (0.0, 4.0, "Hand-thrown"),
        (4.0, 9.0, "Holds 350 ml"),
        (9.0, 13.0, ""),
    ]
    assert ad.captions == []
    assert volumes == [product_shots.MUSIC_ALONE_VOLUME]
    told = results_of("assemble_ad")[-1]
    assert "the music alone" in told
    assert "captions" not in told and "voice" not in told
    assert ad.seconds == 13.0 + END_CARD_SECONDS
    width, height, seconds = video(read(ad.file))
    assert (width, height) == (assembly.FRAME_WIDTH, assembly.FRAME_HEIGHT)
    assert seconds == pytest.approx(13.0 + END_CARD_SECONDS, abs=1.0)


def test_a_changed_overlay_makes_a_new_version_without_filming_again(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)
    producer.told("Put it together", ("assemble_ad", {}), "Here's your ad!")
    fake_model.respond("fact_check", facts_ok(1))

    producer.told(
        "Make scene 1 say Made by hand",
        ("edit_scene", {"scene": 1, "overlay": "Made by hand", "owner_asked": True}),
        ("run_planning_checks", NO_CHOICES),
        ("assemble_ad", {}),
        "Here's the new version.",
    )

    ads = list(
        Job.objects.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version")
    )
    assert [ad.version for ad in ads] == [1, 2]
    assert ads[-1].cuts[0]["overlay"] == "Made by hand"
    assert paid_for().count("make_broll_clip") == 3


def test_an_ad_put_together_again_from_the_same_scenes_is_not_made_twice(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)

    producer.told("Put it together", ("assemble_ad", {}), ("assemble_ad", {}), "Here's your ad!")

    assert Job.objects.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD).count() == 1


def test_a_clip_shorter_than_planned_shortens_the_ad_and_tells_the_developer(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    filmed_product_shots(producer, fake_model, product_page_url)
    ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP, scene__number=1).update(seconds=3.0)

    producer.told("Put it together", ("assemble_ad", {}), "Here's your ad!")

    (ad,) = Job.objects.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD)
    assert [(cut["start"], cut["end"]) for cut in ad.cuts] == [(0.0, 3.0), (3.0, 8.0), (8.0, 12.0)]
    assert any(
        "Scene 1's clip lasts 3 seconds, shorter than the 4 seconds planned" in text
        for _, text in recorded(api, session_id)
    )
