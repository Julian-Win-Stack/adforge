"""The live first-message path uses H3 for every scene, with owner-visible checks."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem

from .conftest import NO_CHOICES, READABLE, ScriptedProducer, facts_ok, handoffs, paid_for
from .test_h3_planning import h3_plan
from .test_h3_scene_words import word_records
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)


def script_h3_scenes(fake: FakeModel, lines: list[str]) -> None:
    fake.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    for index, line in enumerate(lines):
        talking = index != 1
        picture: dict[str, Any] = {
            **PICTURE_FORM,
            "person_and_room": "The woman and room from the shared picture"
            if talking
            else "Hands at a desk",
            "bodies": "One woman with face and hands visible" if talking else "Two hands",
        }
        fake.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", picture)]),
            turn(calls=[("film", FILM)]),
        )
        fake.respond("transcribe_line", {"text": line})


def first_h3_ad(producer: ScriptedProducer, fake: FakeModel, link: str) -> None:
    planned = h3_plan()
    fake.respond("check_page", READABLE)
    fake.respond("plan_ad", planned)
    fake.respond("fact_check", facts_ok(1, 2, 3))
    script_h3_scenes(fake, [scene["line"] for scene in planned["plan"]["scenes"]])
    producer.told(
        link,
        ("read_page", {"link": link, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        ("create_music", {"mood": "light upbeat lo-fi"}),
        [
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Film the planned action.", "quotes": [link]},
            )
            for n in (1, 2, 3)
        ],
        "I'm making your ad.",
    )
    producer.finished_meanwhile()
    producer.finished_meanwhile()
    producer.woken(("assemble_ad", {}), "Here's your ad!")


def test_one_link_makes_every_scene_with_h3_and_no_voice_sample(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    first_h3_ad(producer, fake_model, product_page_url)
    assert paid_for().count("make_broll_clip") == 3
    assert paid_for().count("check_clip_motion") == 3
    assert not {"design_voice", "measure_voice", "speak_line", "make_talking_clip"} & set(
        paid_for()
    )
    assert len(handoffs("transcribe_line")) == 3
    conversation = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert sum(message["role"] == "user" for message in conversation) == 1
    assert not any(a["kind"] == "sound" for m in conversation for a in m["attachments"])
    assert any(
        a["kind"] == "video" and a["scene"] is None for m in conversation for a in m["attachments"]
    )
    assert producer.said()[-1] == "Here's your ad!"
    job = Job.objects.get()
    assert [
        (record["verdict"], record["settled_by"]) for record in word_records(api, session_id)
    ] == [("pass", "code")] * 3
    ad = job.produced.get(kind="finished_ad")
    assert [(cut["start"], cut["end"]) for cut in ad.cuts] == [(0, 9), (9, 18), (18, 27)]
    assert ad.captions
    from .conftest import results_of

    before = paid_for()
    producer.told(
        "Try the old scene tools",
        [
            ("create_person", {}),
            ("make_starting_picture", {"scene": 1, "note": None}),
            ("make_line_audio", {"scene": 1}),
            ("transcribe_line_audio", {"scene": 1}),
            ("make_clip", {"scene": 1}),
        ],
        "Every scene uses its agent.",
    )
    for tool in ("make_starting_picture", "make_line_audio", "transcribe_line_audio", "make_clip"):
        assert "unexpected error" in results_of(tool)[-1].lower()
    assert paid_for() == before


def test_a_voice_change_waits_for_cost_consent_then_refilms_every_scene(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    from agents.models import ToolCall

    first_h3_ad(producer, fake_model, product_page_url)
    old_voice = Job.objects.get().person_voice
    request = {"remake": "voice", "voice": "Warm, low and relaxed", "instruction": None}
    producer.told("Make her voice warmer", ("remake_person", request), "Please confirm the cost.")
    quote = ToolCall.objects.filter(tool="remake_person").latest("pk")
    before = paid_for()
    assert "estimated" in quote.result.lower()
    assert "refilm" in quote.result.lower()
    assert Job.objects.get().person_voice == old_voice
    assert "Yes, change the voice" in " ".join(producer.said())
    approved = {**request, "cost_quote": quote.pk, "confirmation": "Yes, change the voice"}
    for reply in (
        "I won't say Yes, change the voice until I know more",
        "Yes, change the voice?",
        "How much?",
    ):
        producer.told(reply, ("remake_person", {**approved, "confirmation": reply}), "I will wait.")
        assert Job.objects.get().person_voice == old_voice
        assert paid_for() == before
    producer.told(
        "Yes, change the voice",
        ("remake_person", {**approved, "voice": "A different proposal"}),
        "That needs a fresh quote.",
    )
    assert Job.objects.get().person_voice == old_voice
    assert paid_for() == before
    # Editing the ad after its quote cannot authorize the old estimate.
    producer.told(
        "Change scene two's line",
        (
            "edit_scene",
            {
                "scene": 2,
                "line": "Enjoy warm tea from this handmade mug at home, with space for "
                "your favourite drink and easy cleaning afterward.",
            },
        ),
        "I changed the line.",
    )
    producer.told(
        "Yes, change the voice", ("remake_person", approved), "That quote needs updating."
    )
    assert Job.objects.get().person_voice == old_voice
    assert paid_for() == before
    producer.told(
        "Quote the updated ad", ("remake_person", request), "Please confirm the updated cost."
    )
    quote = ToolCall.objects.filter(tool="remake_person").latest("pk")
    approved = {**request, "cost_quote": quote.pk, "confirmation": "Yes, change the voice"}
    producer.told(
        "Yes, change the voice", ("remake_person", approved), "I will refilm every scene."
    )
    assert Job.objects.get().person_voice == "Warm, low and relaxed"
    assert not {"design_voice", "measure_voice"} & set(paid_for())
    producer.told("Put it together now", ("assemble_ad", {}), "I must film the new voice first.")
    assert paid_for() == before
    fake_model.respond("fact_check", facts_ok(2))
    producer.told(
        "Check the changed line", ("run_planning_checks", NO_CHOICES), "The line is checked."
    )
    script_h3_scenes(fake_model, list(Job.objects.get().scenes.values_list("line", flat=True)))
    producer.told(
        "Go ahead",
        [
            (
                "start_scene_agent",
                {
                    "scene": n,
                    "instruction": "Use the new voice.",
                    "quotes": ["Make her voice warmer"],
                },
            )
            for n in (1, 2, 3)
        ],
        "Filming all scenes.",
    )
    producer.finished_meanwhile()
    producer.finished_meanwhile()
    producer.woken(("assemble_ad", {}), "Here is the ad with the new voice.")
    assert paid_for().count("make_broll_clip") == 6
    assert all(
        "says with a woman's voice. Warm, low and relaxed:" in sent["prompt"]
        for sent in handoffs("make_broll_clip")[-3:]
    )


@pytest.mark.parametrize(
    "scene,offer", [(1, "give us a different line"), (2, "holds it and talks about it")]
)
def test_a_give_up_offers_a_way_out_for_its_kind(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    api: APIClient,
    session_id: str,
    scene: int,
    offer: str,
) -> None:
    first_h3_ad(producer, fake_model, product_page_url)
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "A needed view is missing."})])
    )
    producer.told(
        "Try another view",
        (
            "start_scene_agent",
            {"scene": scene, "instruction": "Try the other view.", "quotes": ["Try another view"]},
        ),
        "Trying another view.",
    )
    producer.steps_run()
    shown = str(api.get(f"/api/sessions/{session_id}/messages/").json())
    assert offer in shown
    assert "send a photo" in shown


def test_old_clips_still_assemble_and_a_new_clip_uses_h3(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    settings: Any,
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import results_of

    first_h3_ad(producer, fake_model, product_page_url)
    # Stored audio-backed lineage is historical data; no retired provider executes.
    for clip in ProducedItem.objects.filter(kind="clip"):
        audio = ProducedItem.objects.create(
            job=clip.job, scene=clip.scene, kind="line_audio", version=1, seconds=clip.seconds
        )
        ProducedItem.objects.filter(kind="transcript", made_from=clip).update(made_from=audio)
        clip.made_from = audio
        clip.save(update_fields=["made_from"])

    job = Job.objects.get()
    old_clips = list(job.produced.filter(kind="clip").order_by("pk").values_list("pk", "file"))
    old_sent = list(job.produced.filter(kind="clip").values_list("pk", "sent"))
    old_cuts = job.produced.get(kind="finished_ad").cuts
    producer.told("Put it together again", ("assemble_ad", {}), "It is ready.")
    assert job.produced.get(kind="finished_ad").cuts == old_cuts
    before = paid_for().count("make_talking_clip")
    producer.told(
        "Another try at scene one", ("make_clip", {"scene": 1}), "I will use its scene agent."
    )
    assert "unexpected error" in results_of("make_clip")[-1]
    assert paid_for().count("make_talking_clip") == before
    script_h3_scenes(fake_model, [job.scenes.get(number=1).line])
    producer.told(
        "Try it",
        (
            "start_scene_agent",
            {
                "scene": 1,
                "instruction": "Show the product to the lens while talking.",
                "quotes": ["Another try at scene one"],
            },
        ),
        "Making it.",
    )
    producer.woken(("assemble_ad", {}), "Here is the updated ad.")
    assert paid_for().count("make_talking_clip") == before
    assert paid_for().count("make_broll_clip") == 4
    assert (
        list(
            job.produced.filter(pk__in=[pk for pk, _ in old_clips])
            .order_by("pk")
            .values_list("pk", "file")
        )
        == old_clips
    )
    ad = job.produced.filter(kind="finished_ad").latest("version")
    assert ad.version == 2
    assert all(cut["clip_start"] == 0 for cut in ad.cuts)

    # A later voice change invalidates even the retained historical talking clips,
    # without rewriting their stored sent lists or making old attachments unreadable.
    from agents.models import ToolCall

    request = {"remake": "voice", "voice": "Calm and gentle"}
    producer.told("Change the voice", ("remake_person", request), "Please confirm the cost.")
    quote = ToolCall.objects.filter(tool="remake_person").latest("pk")
    producer.told(
        "Yes, change the voice",
        (
            "remake_person",
            {**request, "cost_quote": quote.pk, "confirmation": "Yes, change the voice"},
        ),
        "Every scene needs filming again.",
    )
    producer.told("Assemble it now", ("assemble_ad", {}), "I must film the new voice first.")
    assert job.produced.filter(kind="finished_ad").count() == 2
    assert "finish" in results_of("assemble_ad")[-1].lower()
    assert (
        list(job.produced.filter(pk__in=[pk for pk, _ in old_sent]).values_list("pk", "sent"))
        == old_sent
    )
    assert api.get(f"/api/sessions/{session_id}/messages/").status_code == 200


def test_old_long_talking_lines_need_shortening_before_new_filming_or_a_voice_quote(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    settings: Any,
) -> None:
    from .conftest import results_of
    from .test_producer_broll import BROLL_PLAN

    # Thirty-six words were legal for a historical talking scene (18 s at its measured pace).
    line = "Meet the Stoneware Mug, " + "a lovely handmade mug for tea " * 5 + "today at home"
    assert len(line.split()) == 37
    # Keep the old maximum exactly: 36 words / 2 measured words a second.
    line = line.removesuffix(" home")
    scenes = [{**BROLL_PLAN["plan"]["scenes"][0], "line": line}, *BROLL_PLAN["plan"]["scenes"][1:]]
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", {**BROLL_PLAN, "plan": {**BROLL_PLAN["plan"], "scenes": scenes}})
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    producer.told(
        product_page_url,
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        "The script is checked.",
    )
    before = paid_for()
    producer.told(
        "Make the voice warmer",
        ("remake_person", {"remake": "voice", "voice": "Warm and relaxed"}),
        "A shorter line is needed first.",
    )
    assert "shorter lines" in results_of("remake_person")[-1]
    producer.told(
        "Film scene one",
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Show it to the lens.", "quotes": ["Film scene one"]},
        ),
        "I need to shorten that line first.",
    )
    assert "shorter line" in results_of("start_scene_agent")[-1]
    assert paid_for() == before


def test_restoring_a_free_h3_version_keeps_its_captions_without_transcribing_again(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    first_h3_ad(producer, fake_model, product_page_url)
    job = Job.objects.get()
    original_captions = job.produced.get(kind="finished_ad").captions
    script_h3_scenes(fake_model, [job.scenes.get(number=1).line])
    producer.told(
        "Try scene one again",
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Try the action again.", "quotes": ["Try scene one again"]},
        ),
        "Making another take.",
    )
    producer.woken(("assemble_ad", {}), "Here is the new take.")
    before = paid_for()
    producer.told(
        "Use scene one's first take",
        ("use_version", {"scene": 1, "version": 1}),
        "The first take is back in the ad.",
    )
    assert paid_for() == before
    assert job.produced.filter(kind="finished_ad").latest("version").captions == original_captions
