"""What is sent to Langfuse: each message's work as a tree of the producer's turns, the
tools each asked for, and the model calls each made. A test keeps what would be sent in
memory instead of sending it, and reads it back as the tree Langfuse would show."""

import json
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

import pytest
from django.apps import apps
from django.conf import settings as django_settings
from django.db.models.signals import post_save, pre_save
from langfuse import Langfuse
from pydantic import BaseModel
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store, tracing
from agents.models import ToolCall
from agents.producer import PRODUCER
from agents.tasks import restart_dead_producers
from chat.models import Session
from evals import agent_page
from gateway.fake import DEFAULT_WHY, FakeModel, turn
from gateway.gateway import VERDICT_SCORES, call_model, submit_broll_clip, watch_video
from gateway.models import ModelCall, SystemPrompt
from gateway.types import Handoff, Judgement
from jobs.clip_judge import (
    MotionFixVerdict,
    MotionVerdict,
    ProductVerdict,
    ReferencesVerdict,
)
from jobs.cut_out import CutOutVerdict, MadePictureVerdict
from jobs.models import Job, SceneStep
from jobs.owner_parts import PartPictureVerdict
from jobs.picture_check import StartingPictureVerdict
from jobs.product_videos import PieceWanted, cut_pieces

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    CLIP_PRODUCT_OK,
    PICTURE_OK,
    READABLE,
    REFERENCES_ENOUGH,
    HeldSteps,
    ScriptedProducer,
    a_video,
    chat,
    openai_turn,
    picture,
    results_of,
)

# The fixture for a chat with a clip made, and those it is built on, for pytest to find.
from .test_end_to_end import first_ad_from_one_link
from .test_producer_clip import clipped, pictured, ready, spoken  # noqa: F401
from .test_producer_music import make_music
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops
from .test_producer_scenes import CHOICE
from .test_round_results import AD, exported
from .traced import Score, Traces

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_messages_work_is_one_trace_of_turns_the_tools_they_asked_for_and_model_calls(
    traces: Traces, page_read: str, session_id: str
) -> None:
    (trace,) = traces.all()

    assert trace.outline() == dedent(
        """\
        producer (agent)
          producer turn (agent)
            produce (generation)
            read_page (tool)
              check_page (generation)
              copy_page_text (generation)
              note_face (generation)
              note_face (generation)
              label_photo (generation)
              label_photo (generation)
          producer turn (agent)
            produce (generation)"""
    )
    assert trace.attributes["session.id"] == session_id
    assert trace.attributes["langfuse.trace.name"] == f"Make an ad for {page_read} · localhost"
    assert list(trace.attributes["langfuse.trace.tags"]) == ["localhost"]
    assert trace.value("input") == {"message": f"Make an ad for {page_read}"}
    assert trace.value("output") == {"said": ["I read your mug's page."]}
    (read_page,) = trace.find(name="read_page")
    # Each side is named, so Langfuse doesn't show them as a chat's "User" and "Assistant".
    assert read_page.value("input") == {
        "arguments": {"link": page_read, "target_seconds": None},
        "why": DEFAULT_WHY,
    }
    assert read_page.value("output") == {"result": results_of("read_page")[0]}
    (check_page,) = trace.find(name="check_page")
    recorded = ModelCall.objects.get(purpose="check_page")
    assert check_page.attributes["langfuse.observation.model.name"] == "gpt-5-mini"
    # With no files shown or made, neither is listed. A call given no tools lists none.
    assert recorded.system_prompt is not None
    assert check_page.value("input") == {
        "instructions": recorded.system_prompt.instructions,
        "handoff": recorded.handoff,
    }
    assert check_page.value("output") == {
        "output": {
            "decision": "readable",
            "reason": "The page names the mug, its price and its size.",
        }
    }
    # The fake bills 1,000 tokens in and 100 out for every call.
    assert check_page.value("usage_details") == {"input": 1_000, "output": 100}
    assert recorded.cost_usd is not None
    assert check_page.value("cost_details") == {"total": float(recorded.cost_usd)}


def test_an_agents_turn_is_shown_with_its_system_prompt_and_the_tools_it_was_offered(
    traces: Traces, page_read: str
) -> None:
    (trace,) = traces.all()
    first, _second = trace.find(name="produce", kind="generation")

    given = first.value("input")
    assert given["instructions"] == PRODUCER.instructions
    offered = {tool["name"]: tool for tool in given["tools"]}
    assert list(offered) == [tool.name for tool in PRODUCER.tools]
    read_page = offered["read_page"]
    assert read_page["description"].startswith("Start a job for an ad")
    assert set(read_page["parameters"]["properties"]) == {"link", "target_seconds", "why"}
    assert given["handoff"]["conversation"] == [
        {"kind": "said", "by": "user", "text": f"Make an ad for {page_read}"}
    ]


def test_an_agents_turn_is_shown_with_the_summary_of_its_reasoning_its_tokens_and_cost(
    traces: Traces,
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    say: Callable[..., None],
) -> None:
    openai_server(
        openai_turn(
            "Send me your product page.",
            reasoning=["The owner said hello.", "I need their product page first."],
        )
    )

    say("Hi")

    (asked,) = [request.get_json() for request, _ in httpserver.log]
    assert asked["reasoning"] == {"summary": "auto"}
    (trace,) = traces.all()
    (produce,) = trace.find(name="produce", kind="generation")
    assert produce.value("output") == {
        "output": {
            "says": "Send me your product page.",
            "calls": [],
            "reasoning": "The owner said hello.\n\nI need their product page first.",
        }
    }
    # The stand-in OpenAI bills 1,200 tokens in and 300 out.
    assert produce.value("usage_details") == {"input": 1_200, "output": 300}
    (recorded,) = ModelCall.objects.filter(purpose="produce")
    assert recorded.cost_usd is not None
    assert produce.value("cost_details") == {"total": float(recorded.cost_usd)}


def test_an_ads_system_prompt_is_stored_once_and_again_only_when_its_text_changes(
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("produce", turn(says="Send me your product page."), turn(says="Thanks!"))
    say("Hi")
    say("Here it comes")
    told_again = replace(PRODUCER, instructions=PRODUCER.instructions + "\n\nBe brief.")
    monkeypatch.setattr("agents.tasks.PRODUCER", told_again)
    fake_model.respond("produce", turn(says="OK."))
    say("And now?")

    turns = ModelCall.objects.filter(purpose="produce").select_related("system_prompt")
    stored = [turn.system_prompt for turn in turns]
    assert all(prompt is not None for prompt in stored)
    first, second, third = stored
    assert first == second != third
    assert SystemPrompt.objects.filter(session_id=session_id, purpose="produce").count() == 2
    assert [prompt.instructions for prompt in (first, third) if prompt] == [
        PRODUCER.instructions,
        told_again.instructions,
    ]
    assert first is not None
    assert [tool["name"] for tool in first.tools] == [tool.name for tool in PRODUCER.tools]


def test_every_part_of_a_trace_is_sent_in_the_environment_settings_name(
    traces: Traces, page_read: str
) -> None:
    (trace,) = traces.all()

    environment = django_settings.LANGFUSE_ENVIRONMENT
    # Langfuse's own when none is set.
    assert environment not in ("", "default")
    assert {part.attributes["langfuse.environment"] for part in trace.find()} == {environment}


@pytest.mark.parametrize(
    ("public_key", "secret_key", "missing"),
    [
        ("", "", "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are"),
        ("pk-lf-test", "", "LANGFUSE_SECRET_KEY is"),
        ("", "sk-lf-test", "LANGFUSE_PUBLIC_KEY is"),
    ],
)
def test_the_app_warns_as_it_starts_when_tracing_is_off_for_a_missing_key(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    public_key: str,
    secret_key: str,
    missing: str,
) -> None:
    settings.LANGFUSE_PUBLIC_KEY = public_key
    settings.LANGFUSE_SECRET_KEY = secret_key

    apps.get_app_config("gateway").ready()

    assert f"Langfuse tracing is off: {missing} not set, so nothing is traced" in [
        record.getMessage() for record in caplog.records if record.levelname == "WARNING"
    ]


def test_the_app_says_nothing_of_tracing_as_it_starts_when_both_keys_are_set(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    settings.LANGFUSE_PUBLIC_KEY = "pk-lf-test"
    settings.LANGFUSE_SECRET_KEY = "sk-lf-test"

    apps.get_app_config("gateway").ready()

    assert not [record for record in caplog.records if "Langfuse" in record.getMessage()]


def test_each_message_starts_a_new_trace_named_after_its_ad_and_grouped_with_its_session(
    traces: Traces, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="Send me your product page."), turn(says="Thanks!"))

    say("Hi")
    say("Here it comes")

    first, second = traces.all()
    assert first.trace_id != second.trace_id
    assert [each.attributes["langfuse.trace.name"] for each in (first, second)] == ["Hi"] * 2
    assert [list(each.attributes["langfuse.trace.tags"]) for each in (first, second)] == [
        ["Hi"]
    ] * 2
    assert [each.value("input") for each in (first, second)] == [
        {"message": "Hi"},
        {"message": "Here it comes"},
    ]
    # Both are the session's, so Langfuse shows the chat's work together.
    assert [each.attributes["session.id"] for each in (first, second)] == [session_id] * 2


def test_a_renamed_sessions_next_trace_takes_its_new_name_and_tag(
    traces: Traces,
    fake_model: FakeModel,
    page_read: str,
    planned: None,
    session_id: str,
    api: APIClient,
    say: Callable[..., None],
) -> None:
    renamed = api.patch(f"/api/sessions/{session_id}/", {"name": "round-7 n13"}, format="json")
    assert renamed.status_code == 200
    fake_model.respond("produce", turn(says="Ready."))
    say("Carry on")

    *_, first, second = traces.all()
    assert first.trace_id != second.trace_id
    assert second.attributes["langfuse.trace.name"] == "round-7 n13 · Stoneware Mug"
    assert set(second.attributes["langfuse.trace.tags"]) == {"round-7 n13", "localhost"}
    assert all(
        page_read not in tag
        for each in (first, second)
        for tag in each.attributes["langfuse.trace.tags"]
    )


def test_the_product_site_comes_from_the_pages_final_address(
    traces: Traces,
    fake_model: FakeModel,
    page_read: str,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    job.page_final_url = "https://www.shop.example/products/mug"
    job.product_url = "https://redirect.example/mug"
    job.save(update_fields=["page_final_url", "product_url"])
    fake_model.respond("produce", turn(says="Ready."))
    say("Carry on")

    assert list(traces.all()[-1].attributes["langfuse.trace.tags"]) == ["shop.example"]


@pytest.mark.parametrize(
    ("ad", "name", "tags"),
    [
        (tracing.Ad(), "ad s", set()),
        (
            tracing.Ad("  round-7 n13  ", "  ", "shop.example"),
            "round-7 n13 · shop.example",
            {"round-7 n13", "shop.example"},
        ),
        (tracing.Ad("Mug", "Mug", "shop.example"), "Mug", {"Mug", "shop.example"}),
        (
            tracing.Ad("https://shop.example/product", "Mug", "shop.example"),
            "https://shop.example/product · Mug",
            {"shop.example"},
        ),
    ],
)
def test_trace_names_use_the_known_ad_with_a_fallback_and_no_link_tags(
    traces: Traces,
    ad: tracing.Ad,
    name: str,
    tags: set[str],
) -> None:
    with tracing.message_work(
        "producer", session_id="s", ad=ad, message="Make it", trace_id="", woken_by=[]
    ):
        pass

    (trace,) = traces.all()
    assert trace.attributes["langfuse.trace.name"] == name
    assert set(trace.attributes.get("langfuse.trace.tags", ())) == tags


def test_a_scene_step_lands_under_the_tool_call_that_started_it_with_what_it_was_asked_and_made(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    # Once the producer's work on the message is over, as a step run by another worker is.
    steps.run_held()

    (tool,) = [
        found
        for trace in traces.all()
        for found in trace.find(name="make_starting_picture", kind="tool")
    ]
    assert tool.outline() == dedent(
        """\
        make_starting_picture (tool)
          scene 1 · starting picture (chain)
            choose_starting_picture (generation)
            make_starting_picture (generation)"""
    )
    # The readable step name also keeps its scene and kind as metadata.
    (step,) = tool.inside
    assert step.metadata() == {"scene": 1, "step": "starting_picture"}
    made = SceneStep.objects.get()
    assert step.value("input") == {
        "kind": "starting_picture",
        "scene": 1,
        "line": made.line,
        "prompt": CHOICE["prompt"],
    }
    assert step.value("output") == {"result": made.result}


def test_a_scene_step_that_fails_still_shows_what_it_was_asked(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": "Hold it by the handle."})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", ValueError("The writer broke."))
    fake_model.respond("produce", turn(says="Scene 1's starting picture failed."))

    steps.run_held()

    (step,) = [
        found for trace in traces.all() for found in trace.find(name="scene 1 · starting picture")
    ]
    assert step.attributes["langfuse.observation.level"] == "ERROR"
    assert step.value("input") == {
        "kind": "starting_picture",
        "scene": 1,
        "line": SceneStep.objects.get().line,
        "note": "Hold it by the handle.",
    }


def test_a_messages_work_woken_again_by_a_scene_step_stays_in_the_messages_trace(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    steps.run_held()

    # After those of the messages that made the plan and the person.
    *_, first, woken = traces.all()
    assert woken.trace_id == first.trace_id
    # The part keeps the agent's name: what woke it is in its metadata.
    assert (first.name, woken.name) == ("producer", "producer")
    session = Session.objects.get()
    expected = f"{session.name} · Stoneware Mug"
    assert first.attributes["langfuse.trace.name"] == expected
    assert woken.attributes["langfuse.trace.name"] == expected
    assert "localhost" in woken.attributes["langfuse.trace.tags"]
    assert woken.metadata() == {"woken_by": ["scene 1's starting picture"]}
    assert woken.value("input") == {
        "message": "Make scene 1's starting picture",
        "woken_by": ["scene 1's starting picture"],
    }
    assert woken.value("output") == {"said": ["Scene 1's starting picture is ready!"]}
    assert woken.outline() == dedent(
        """\
        producer (agent)
          producer turn (agent)
            produce (generation)"""
    )


def test_a_scene_step_lands_under_its_tool_call_run_again_after_the_worker_stopped(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("make_starting_picture", {"scene": 1, "note": None})])
    )
    # The step is started, then the worker stops before the tool's checkpoint is finished.
    tool_finishing = the_worker_stops(
        pre_save,
        ToolCall,
        when=lambda call: call.tool == "make_starting_picture" and call.finished_at is not None,
    )
    with tool_finishing, pytest.raises(WorkerStopped):
        say("Make scene 1's starting picture")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I've started scene 1's picture."))
    restart_dead_producers()
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    steps.run_held()

    first, again = [
        found
        for trace in traces.all()
        for found in trace.find(name="make_starting_picture", kind="tool")
    ]
    assert first.inside == []
    assert [step.name for step in again.inside] == ["scene 1 · starting picture"]


def kinds_of_file(sent: list[str]) -> list[str]:
    """What kind of file each of the files sent is, from the reference Langfuse shows it by."""
    kinds = []
    for reference in sent:
        found = re.fullmatch(r"@@@langfuseMedia:type=([^|]+)\|id=[^|]+\|source=bytes@@@", reference)
        assert found, f"{reference!r} isn't a file Langfuse shows"
        kinds.append(found.group(1))
    return kinds


def test_pictures_and_audio_given_to_or_made_by_a_model_are_shown_in_the_trace(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))
    steps.run_held()

    calls = {found.name: found for trace in traces.all() for found in trace.find(kind="generation")}
    assert kinds_of_file(calls["draw_person"].value("output")["made"]) == ["image/png"]
    assert kinds_of_file(calls["measure_voice"].value("output")["made"]) == ["audio/wav"]
    # The portrait and the photos it chooses between, then the portrait and the chosen photo
    # it is made from.
    assert (
        kinds_of_file(calls["choose_starting_picture"].value("input")["shown"]) == ["image/png"] * 3
    )
    made = calls["make_starting_picture"]
    assert kinds_of_file(made.value("input")["shown"]) == ["image/png"] * 2
    assert kinds_of_file(made.value("output")["made"]) == ["image/png"]


def test_a_turn_answered_from_its_record_shows_as_such_with_no_cost(
    traces: Traces,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    read_page = ("read_page", {"link": product_page_url, "target_seconds": None})
    fake_model.respond("produce", turn(says="I'll read your mug's page.", calls=[read_page]))
    turn_recorded = the_worker_stops(
        post_save, ModelCall, when=lambda call: call.purpose == "produce"
    )
    with turn_recorded, pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    the_producer_died(session_id)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(says="I read your mug's page."))

    restart_dead_producers()

    stopped, again = traces.all()
    assert (again.name, again.trace_id) == ("producer", stopped.trace_id)
    assert again.metadata() == {"started_again": True}
    assert [call.name for call in stopped.find(kind="generation")] == ["produce"]
    (answered, checked_after, copied_after, *noted, paid) = again.find(kind="generation")
    assert (answered.name, checked_after.name, copied_after.name, paid.name) == (
        "produce (answered from its record)",
        "check_page",
        "copy_page_text",
        "produce",
    )
    assert [call.name for call in noted] == [
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
    ]
    assert answered.value("cost_details") == {"total": 0}
    paid_before = ModelCall.objects.filter(purpose="produce").first()
    assert paid_before is not None
    assert answered.value("output") == {"output": paid_before.output}


class Broken:
    """A Langfuse client whose every use fails."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Langfuse is broken")


def test_tracing_that_fails_never_fails_a_tool_a_turn_or_a_scene_step(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    with tracing.use_client(cast(Langfuse, Broken())):
        say("Make scene 1's starting picture")
        steps.run_held()

    assert results_of("make_starting_picture") == [
        "Started scene 1's starting picture. It isn't made yet: you'll be told when it's ready."
    ]
    assert SceneStep.objects.get().status == "finished"
    assert chat(api, session_id)[-1] == ("agent", "Scene 1's starting picture is ready!")


def a_message_with_a_tool(fake_model: FakeModel, product_page_url: str) -> None:
    """Script the producer to read the page, then reply."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)


def test_langfuse_refusing_the_keys_never_fails_the_work(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    httpserver: HTTPServer,
    settings: Settings,
) -> None:
    settings.LANGFUSE_PUBLIC_KEY = f"pk-lf-wrong-{uuid.uuid4()}"
    settings.LANGFUSE_SECRET_KEY = "sk-lf-wrong"
    settings.LANGFUSE_BASE_URL = httpserver.url_for("/langfuse")
    httpserver.expect_request(re.compile("^/langfuse/")).respond_with_data("Unauthorized", 401)
    a_message_with_a_tool(fake_model, product_page_url)

    say(f"Make an ad for {product_page_url}")

    # It was sent, and refused.
    assert any(request.path.startswith("/langfuse/") for request, _ in httpserver.log)
    assert chat(api, session_id)[-1] == ("agent", "I read your mug's page.")


def test_with_no_langfuse_keys_nothing_is_sent(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    httpserver: HTTPServer,
    settings: Settings,
) -> None:
    settings.LANGFUSE_BASE_URL = httpserver.url_for("/langfuse")
    a_message_with_a_tool(fake_model, product_page_url)

    say(f"Make an ad for {product_page_url}")

    assert not [request for request, _ in httpserver.log if request.path.startswith("/langfuse")]


def test_a_clip_and_the_music_made_are_shown_in_the_trace(
    traces: Traces,
    fake_model: FakeModel,
    clipped: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    make_music(fake_model, say, "Light upbeat lo-fi.")

    calls = {found.name: found for trace in traces.all() for found in trace.find(kind="generation")}
    # The clip is asked for from the starting picture and the line's audio, then collected.
    assert kinds_of_file(calls["make_talking_clip"].value("input")["shown"]) == [
        "image/png",
        "audio/wav",
    ]
    assert kinds_of_file(calls["collect_talking_clip"].value("output")["made"]) == ["video/mp4"]
    assert kinds_of_file(calls["transcribe_line"].value("input")["shown"]) == ["audio/wav"]
    assert kinds_of_file(calls["make_music"].value("output")["made"]) == ["audio/mp4"]


def test_a_piece_of_video_a_b_roll_clip_is_sent_is_shown_in_the_trace(
    traces: Traces, fake_model: FakeModel, tmp_path: Path
) -> None:
    made = tmp_path / "page.mp4"
    subprocess.run(
        [django_settings.FFMPEG, "-loglevel", "error", "-f", "lavfi"]
        + ["-i", "testsrc=size=32x48:rate=5"]
        + ["-t", "4", "-pix_fmt", "yuv420p", str(made)],
        check=True,
    )
    page_video = file_store.save("page.mp4", made.read_bytes())

    submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=None,
        example_picture_keys=[file_store.save("photo.png", picture(72, 128, (1, 2, 3)))],
        reference_videos=cut_pieces([PieceWanted(video=page_video, start=1, end=3)]).sent,
        seconds=5,
        prompt="Tea is poured into the mug.",
    )

    calls = {found.name: found for trace in traces.all() for found in trace.find(kind="generation")}
    assert kinds_of_file(calls["make_broll_clip"].value("input")["shown"]) == [
        "image/png",
        "video/mp4",
    ]


class Moments(BaseModel):
    moments: list[str]


class Told(Handoff):
    product_name: str


def test_a_video_watched_is_shown_in_the_trace_as_it_was_sent(
    traces: Traces, fake_model: FakeModel
) -> None:
    key = file_store.save("videos/page-video.mp4", a_video(540, 960, sound=True))
    fake_model.respond("watch_video", {"moments": ["The lid is twisted off."]})

    watch_video(
        job=None,
        purpose="watch_video",
        instructions="Watch the product's video.",
        handoff=Told(product_name="Stoneware Mug"),
        video_key=key,
        output=Moments,
    )

    (call,) = [found for trace in traces.all() for found in trace.find(name="watch_video")]
    assert call.kind == "generation"
    assert call.value("input")["handoff"] == {"product_name": "Stoneware Mug"}
    assert kinds_of_file(call.value("input")["shown"]) == ["video/mp4"]
    assert call.value("output")["output"] == {"moments": ["The lid is twisted off."]}


class Asked(Handoff):
    ask: int


SUDDEN_CHANGE = "The mug's handle jumps to the other side at 2 s."


@pytest.mark.parametrize(
    ("purpose", "output", "answer", "scored"),
    [
        (
            "check_starting_picture",
            StartingPictureVerdict,
            {**PICTURE_OK, "verdict": "fail", "problem": "The handle is inside the mug."},
            [Score("starting_picture_real", "fail", "The handle is inside the mug.")],
        ),
        (
            "check_clip_references",
            ReferencesVerdict,
            {
                **REFERENCES_ENOUGH,
                "verdict": "product not shown",
                "references_never_show": "the references never show its base",
            },
            [Score("references_enough", "product not shown", "the references never show its base")],
        ),
        (
            "check_clip_product",
            ProductVerdict,
            {**CLIP_PRODUCT_OK, "verdict": "unsure"},
            [Score("clip_matches_product", "unsure", None)],
        ),
        (
            "check_clip_motion",
            MotionFixVerdict,
            {
                **CLIP_MOTION_OK,
                "sudden_change": {
                    "verdict": "fail",
                    "problem": SUDDEN_CHANGE,
                    "back_to_normal_at": None,
                },
                "owner_change": CLIP_FIXED,
            },
            [
                Score("clip_real_motion", "pass", None),
                Score("clip_no_sudden_change", "fail", SUDDEN_CHANGE),
                Score("clip_does_what_was_ordered", "pass", None),
                Score("clip_owner_change_made", "fixed", None),
            ],
        ),
    ],
)
def test_a_judges_verdict_is_a_score_on_its_call_named_after_what_it_measures(
    traces: Traces,
    fake_model: FakeModel,
    purpose: str,
    output: type[BaseModel],
    answer: dict[str, Any],
    scored: list[Score],
) -> None:
    fake_model.respond(purpose, answer)

    call_model(
        job=None, purpose=purpose, instructions="Judge it.", handoff=Asked(ask=1), output=output
    )

    (call,) = [found for trace in traces.all() for found in trace.find(kind="generation")]
    assert traces.scores(call) == scored
    judged = "clip" if purpose.startswith("check_clip_") else "picture"
    expected = {
        f"{judged}-{'unsure' if score.value == 'unsure' else 'fail'}"
        for score in scored
        if score.value not in {"pass", "enough", "fixed", "not_checked"}
    }
    assert set(call.attributes.get("langfuse.trace.tags", ())) == expected


class RecordedVerdict(BaseModel):
    # The gateway can read older recorded verdicts too, including picture "unsure".
    verdict: str


@pytest.mark.parametrize(
    ("purpose", "verdict", "expected"),
    [
        ("check_clip_product", "fail", {"clip-fail"}),
        ("check_clip_product", "unsure", {"clip-unsure"}),
        ("check_clip_product", "pass", set()),
        ("check_starting_picture", "fail", {"picture-fail"}),
        ("check_starting_picture", "unsure", {"picture-unsure"}),
        ("check_starting_picture", "pass", set()),
        ("check_cut_out", "fail", {"picture-fail"}),
        ("check_made_picture", "unsure", {"picture-unsure"}),
        ("check_part_picture", "fail", {"picture-fail"}),
        ("check_clip_references", "product not shown", {"clip-fail"}),
        ("check_clip_references", "enough", set()),
        ("check_clip_product", "fixed", set()),
        ("check_clip_product", "not_checked", set()),
    ],
)
def test_judge_verdicts_tag_the_trace_without_changing_the_scores_or_answer(
    traces: Traces,
    fake_model: FakeModel,
    purpose: str,
    verdict: str,
    expected: set[str],
) -> None:
    fake_model.respond(purpose, {"verdict": verdict})
    with tracing.message_work(
        "producer",
        session_id="s",
        ad=tracing.Ad("round-7 n13"),
        message="Make it",
        trace_id="",
        woken_by=[],
    ):
        call_model(
            job=None,
            purpose=purpose,
            instructions="Judge it.",
            handoff=Asked(ask=1),
            output=RecordedVerdict,
        )

    (trace,) = traces.all()
    (call,) = trace.find(kind="generation")
    verdict_tags = {
        tag
        for part in trace.find()
        for tag in part.attributes.get("langfuse.trace.tags", ())
        if tag.endswith(("-fail", "-unsure"))
    }
    assert verdict_tags == expected
    assert "round-7 n13" in trace.attributes["langfuse.trace.tags"]
    assert call.value("output")["output"] == {"verdict": verdict}
    assert [score.value for score in traces.scores(call)] == [verdict]


# Every judge's answer forms, by the purpose its calls are made for.
JUDGES_ANSWERS: dict[str, list[type[BaseModel]]] = {
    "check_starting_picture": [StartingPictureVerdict],
    "check_cut_out": [CutOutVerdict],
    "check_made_picture": [MadePictureVerdict],
    "check_part_picture": [PartPictureVerdict],
    "check_clip_product": [ProductVerdict],
    "check_clip_references": [ReferencesVerdict],
    "check_clip_motion": [MotionVerdict, MotionFixVerdict],
}


def test_every_scored_verdict_is_where_each_judges_answer_form_puts_it() -> None:
    """A verdict renamed or moved in an answer form would lose its score without a word."""
    assert set(VERDICT_SCORES) == set(JUDGES_ANSWERS)
    for purpose, scores in VERDICT_SCORES.items():
        for path in scores.values():
            # Each path is in at least one of the judge's forms: a fix's question is only in
            # a fix's.
            found = []
            for form in JUDGES_ANSWERS[purpose]:
                schema = form.model_json_schema()
                part: dict[str, Any] | None = schema
                for key in path:
                    field = (part or {}).get("properties", {}).get(key)
                    ref = (field or {}).get("$ref", "")
                    part = schema["$defs"].get(ref.removeprefix("#/$defs/")) if ref else field
                found.append(part is not None and "verdict" in part.get("properties", {}))
            assert any(found), f"{purpose}'s {path} names no verdict"


def test_a_call_that_judges_nothing_is_not_scored(traces: Traces, fake_model: FakeModel) -> None:
    fake_model.respond("check_page", READABLE)

    call_model(
        job=None,
        purpose="check_page",
        instructions="Is it readable?",
        handoff=Asked(ask=1),
        output=Judgement,
    )

    (call,) = [found for trace in traces.all() for found in trace.find(kind="generation")]
    assert traces.scores(call) == []


# --- Spec #247: a whole ad's traces and record -------------------------------------------------

LOOK_WHY = "The shop owner asks about scene 2, so I look at its clip before I answer."
REASONING = "The shop owner sent a link and nothing else, so I make the whole ad."
LOOKED = {
    "sees": "The mug turns on a workbench while tea pours in.",
    "wrong": "Nothing",
    "root_cause": "",
    "direction": "",
}


def test_a_whole_ad_keeps_every_calls_why_lists_them_after_the_first_clip_and_traces_them(
    traces: Traces,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    tmp_path: Path,
) -> None:
    fake_model.reasoning = REASONING
    first_ad_from_one_link(producer, fake_model, product_page_url)
    fake_model.respond("look", LOOKED)
    producer.told(
        "Is scene 2 right?",
        (
            "look",
            {
                "scene": 2,
                "what": "clip",
                "number": None,
                "question": "Does the clip show the mug as planned?",
                "why": LOOK_WHY,
            },
        ),
        "Scene 2 shows the mug as planned.",
    )

    # Every tool call the producer's turns made keeps its row and its why.
    calls = ToolCall.objects.filter(agent="producer")
    taken = ModelCall.objects.filter(purpose="produce")
    made = [len((each.output or {})["calls"]) for each in taken]
    assert calls.count() == sum(made)
    assert calls.count() > 0
    assert set(calls.exclude(tool="look").values_list("why", flat=True)) == {DEFAULT_WHY}
    assert calls.get(tool="look").why == LOOK_WHY

    # The ad's page lists the calls after the first clip, each with its why, cost and time.
    run = exported(tmp_path)
    listed = json.loads((run / AD / "tool_calls.json").read_text())
    tools = [each["tool"] for each in listed if each["agent"] == "producer"]
    assert "read_page" not in tools and "plan_ad" not in tools
    assert tools[-2:] == ["assemble_ad", "look"]
    (look,) = [each for each in listed if each["tool"] == "look"]
    assert look["why"] == LOOK_WHY
    assert Decimal(look["cost_usd"]) > 0
    assert look["created_at"] and look["finished_at"]
    shown = agent_page.page(run, AD)
    assert LOOK_WHY in shown
    assert f"${look['cost_usd']} · started" in shown

    everything = traces.all()
    # Later parts of the first message, and the next message, carry the known product.
    session = Session.objects.get()
    expected = f"{session.name} · Stoneware Mug"
    assert everything[-1].attributes["langfuse.trace.name"] == expected
    assert "localhost" in everything[-1].attributes["langfuse.trace.tags"]
    assert all(
        part.attributes["langfuse.trace.name"] == expected
        for each in everything
        for part in each.find(kind="chain")
    )
    environment = django_settings.LANGFUSE_ENVIRONMENT
    assert {
        part.attributes["langfuse.environment"] for each in everything for part in each.find()
    } == {environment}
    # Each producer turn is shown with its system prompt and the summary of its reasoning.
    turns = [part for each in everything for part in each.find(name="produce")]
    assert len(turns) == ModelCall.objects.filter(purpose="produce").count()
    assert {part.value("input")["instructions"] for part in turns} == {PRODUCER.instructions}
    assert {part.value("output")["output"]["reasoning"] for part in turns} == {REASONING}
    # The judge's verdicts on scene 2's clip are scores on its calls.
    (motion,) = [part for each in everything for part in each.find(name="check_clip_motion")]
    assert [score.name for score in traces.scores(motion)] == [
        "clip_real_motion",
        "clip_no_sudden_change",
        "clip_does_what_was_ordered",
    ]
    # Scene 2's clip step is shown with what it was asked and what it made.
    (clip,) = [
        part
        for each in everything
        for part in each.find(name="scene 2 · clip", kind="chain")
        if part.metadata() == {"scene": 2, "step": "clip"}
    ]
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert clip.value("input")["kind"] == "clip" and clip.value("input")["scene"] == 2
    assert clip.value("output") == {"result": step.result}
