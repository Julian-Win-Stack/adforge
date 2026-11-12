"""Azure safety blocks fall back to OpenAI at the gateway, through the owner's chat."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.gateway import use_model
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest, Turn, TurnRequest
from jobs.models import Job

from .conftest import (
    FACTS_OK,
    PLAN,
    READABLE,
    HeldSteps,
    OpenAIText,
    a_plan_with,
    chat,
    openai_turn,
    recorded,
    results_of,
)
from .test_producer_broll import calling, run
from .test_producer_checks import checking
from .test_safety_filter_split import five_photos_page, refused_over_picture  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("reason", ["sexual, medium", "image content_policy_violation"])
def test_azure_refused_fact_check_is_answered_on_openai(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
    sentry_events: list[dict[str, Any]],
    reason: str,
    steps: HeldSteps,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    checking(
        fake_model,
        product_page_url,
        PLAN,
        after_checks=[
            turn(
                calls=[
                    (
                        "start_scene_agent",
                        {
                            "scene": 2,
                            "instruction": "Make the planned scene.",
                            "quotes": [product_page_url],
                        },
                    )
                ]
            )
        ],
    )
    fake_model.respond("fact_check", BlockedBySafetyFilter(reason), provider="azure")
    fake_model.respond("fact_check", FACTS_OK, provider="openai")

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert len(steps.held) == 1
    calls = list(ModelCall.objects.filter(purpose="fact_check"))
    assert [(call.provider, call.outcome) for call in calls] == [
        ("azure-openai", "failed"),
        ("openai", "succeeded"),
    ]
    azure, openai = calls
    assert openai.handoff == azure.handoff
    assert openai.images == azure.images
    assert openai.system_prompt_id == azure.system_prompt_id
    assert openai.model == azure.model
    assert openai.cost_usd is not None and openai.cost_usd > 0
    notices = [text for _, text in recorded(api, session_id) if reason in text]
    assert len(notices) == 1
    assert "fact_check" in notices[0]
    assert not any(notices[0] in text for _, text in chat(api, session_id))
    assert [
        event["extra"]["text"]
        for event in sentry_events
        if event["tags"]["kind"] == "text_on_openai"
    ] == notices


def test_both_services_refusing_photos_keeps_sheet_splitting_on_openai(
    fake_model: FakeModel,
    five_photos_page: str,  # noqa: F811
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": five_photos_page, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn("Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", BlockedBySafetyFilter("image blocked"), provider="azure")
    fake_model.answer_unscripted("plan_ad", refused_over_picture(3, PLAN), provider="openai")

    say(five_photos_page)

    assert results_of("plan_ad")[0].startswith("Planned")
    calls = list(ModelCall.objects.filter(purpose="plan_ad"))
    assert calls[0].provider == "azure-openai" and calls[0].outcome == "failed"
    assert all(call.provider == "openai" for call in calls[1:])
    assert sum(call.outcome == "failed" for call in calls[1:]) > 1
    assert sum(call.outcome == "succeeded" for call in calls[1:]) > 1
    assert calls[1].images == calls[0].images
    fallback = [text for _, text in recorded(api, session_id) if "Azure's safety filter" in text]
    assert len(fallback) == 1
    assert any("library picture 3" in text for _, text in recorded(api, session_id))
    assert not any(fallback[0] == text for _, text in chat(api, session_id))


def test_a_fact_check_refused_by_both_services_never_counts_as_checked(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    reply = "Send the link for another product."
    checking(fake_model, product_page_url, PLAN, reply=reply)
    fake_model.respond("fact_check", BlockedBySafetyFilter("text blocked"), provider="azure")

    def refused(_: ModelRequest[Any]) -> dict[str, Any]:
        raise BlockedBySafetyFilter("text blocked")

    fake_model.answer_unscripted("fact_check", refused, provider="openai")

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("Failed:")
    assert not Job.objects.get().scenes.filter(fact_checked=True).exists()
    calls = list(ModelCall.objects.filter(purpose="fact_check"))
    assert calls[0].provider == "azure-openai"
    assert calls[1:] and all(call.provider == "openai" for call in calls[1:])
    assert all(call.outcome == "failed" for call in calls)
    stopped = [
        warning
        for warning in api.get(f"/api/sessions/{session_id}/warnings/").json()
        if warning["kind"] == "ad_blocked_by_filter"
    ]
    assert len(stopped) == 1
    assert "isn't your fault" in stopped[0]["text"]
    assert "try another product" in stopped[0]["text"].lower()
    assert [
        text
        for role, text in chat(api, session_id)
        if role == "notice" and text == stopped[0]["text"]
    ] == [stopped[0]["text"]]
    last_call = ModelCall.objects.last()
    assert last_call is not None and last_call.purpose == "fact_check"
    assert ModelCall.objects.filter(purpose="produce").count() == 4
    assert any(
        "run_planning_checks" in text and "text blocked" in text
        for _, text in recorded(api, session_id)
    )

    # The queued reply was never taken after the block; the next owner message wakes it.
    say("Let's try a different product")
    assert chat(api, session_id)[-1] == ("agent", reply)
    last_call = ModelCall.objects.last()
    assert last_call is not None and last_call.provider == "azure-openai"


def test_both_services_refuse_a_fact_check_photo_then_it_passes_on_openai_without_it(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    checking(fake_model, product_page_url, a_plan_with(colour_photos=[1, 2]))
    fake_model.respond("fact_check", BlockedBySafetyFilter("image blocked"), provider="azure")
    refused_photo: str | None = None

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        nonlocal refused_photo
        if refused_photo is None:
            assert request.images
            refused_photo = request.images[-1].label
        if refused_photo in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("image blocked")
        return FACTS_OK

    fake_model.answer_unscripted("fact_check", answering, provider="openai")

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    calls = list(ModelCall.objects.filter(purpose="fact_check"))
    assert [(call.provider, call.outcome) for call in calls] == [
        ("azure-openai", "failed"),
        ("openai", "failed"),
        ("openai", "succeeded"),
    ]
    assert calls[0].images == calls[1].images
    assert calls[2].images and len(calls[2].images) == len(calls[0].images) - 1
    assert Job.objects.get().scenes.filter(fact_checked=True).count() == 3
    assert refused_photo is not None
    notices = [
        text
        for _, text in recorded(api, session_id)
        if "Azure's safety filter" in text or refused_photo in text
    ]
    assert len(notices) == 2
    assert all(text not in [message for _, message in chat(api, session_id)] for text in notices)


def test_an_azure_blocked_answer_is_remade_on_openai_and_every_attempt_is_billed(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    checking(fake_model, product_page_url, PLAN)

    def filtered() -> BlockedBySafetyFilter:
        return BlockedBySafetyFilter(
            "content_filter", of_what_was_made=True, input_tokens=1_000, output_tokens=100
        )

    fake_model.respond("fact_check", filtered(), provider="azure")
    fake_model.respond("fact_check", filtered(), FACTS_OK, provider="openai")

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    calls = list(ModelCall.objects.filter(purpose="fact_check"))
    assert [(call.provider, call.outcome) for call in calls] == [
        ("azure-openai", "failed"),
        ("openai", "failed"),
        ("openai", "succeeded"),
    ]
    assert all(call.cost_usd is not None and call.cost_usd > 0 for call in calls)
    assert all(call.images == calls[0].images for call in calls)


def test_a_producer_turn_refused_by_azure_is_taken_on_openai_then_azure_resumes(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    fake_model.respond("produce", BlockedBySafetyFilter("text blocked"), provider="azure")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn("I read the page."),
    )
    fake_model.respond("check_page", READABLE)

    say(product_page_url)

    assert chat(api, session_id)[-1] == ("agent", "I read the page.")
    calls = list(ModelCall.objects.filter(purpose="produce"))
    assert [call.provider for call in calls] == ["azure-openai", "openai", "azure-openai"]
    assert calls[0].handoff == calls[1].handoff
    assert calls[0].system_prompt_id == calls[1].system_prompt_id


def test_both_refused_turns_retry_on_openai_until_answered(
    fake_model: FakeModel,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
    sentry_events: list[dict[str, Any]],
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    settings.PRODUCER_FILTER_RETRY_SECONDS = [0, 0]
    fake_model.respond("produce", BlockedBySafetyFilter("text blocked"), provider="azure")
    fake_model.respond(
        "produce",
        BlockedBySafetyFilter("text blocked"),
        BlockedBySafetyFilter("text blocked"),
        turn("What product shall we make an ad for?"),
        provider="openai",
    )

    say("Hello")

    assert chat(api, session_id)[-1] == ("agent", "What product shall we make an ad for?")
    assert list(ModelCall.objects.filter(purpose="produce").values_list("provider", flat=True)) == [
        "azure-openai",
        "openai",
        "openai",
        "openai",
    ]
    assert sum(event["tags"]["kind"] == "text_on_openai" for event in sentry_events) == 1


def test_without_azure_a_block_keeps_the_existing_openai_path(
    fake_model: FakeModel,
    say: Callable[..., None],
    settings: Settings,
    sentry_events: list[dict[str, Any]],
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = False
    fake_model.respond("produce", BlockedBySafetyFilter("text blocked"), provider="openai")

    say("Hello")

    [call] = ModelCall.objects.filter(purpose="produce")
    assert call.provider == "fake" and call.outcome == "failed"
    assert not any(event["tags"]["kind"] == "text_on_openai" for event in sentry_events)


def test_one_scenes_refused_turn_does_not_change_another_scenes_provider(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    fake_model.respond(
        "scene_agent",
        BlockedBySafetyFilter("scene 1 blocked"),
        turn(calls=[("give_up", {"reason": "Prepare another view."})]),
        provider="azure",
    )

    def refused(_: TurnRequest) -> Turn:
        raise BlockedBySafetyFilter("scene 1 blocked")

    fake_model.answer_unscripted("scene_agent", refused, provider="openai")
    fake_model.answer_unscripted("produce", lambda _: turn("The scene work settled."))
    calling(
        fake_model,
        say,
        *[
            ("start_scene_agent", {"scene": scene, "instruction": "Show it.", "quotes": ["Go on"]})
            for scene in (1, 2)
        ],
    )

    run(fake_model, steps)

    first = list(
        ModelCall.objects.filter(purpose="scene_agent", tool_call__agent_fix__step__scene__number=1)
    )
    second = list(
        ModelCall.objects.filter(purpose="scene_agent", tool_call__agent_fix__step__scene__number=2)
    )
    assert [(call.provider, call.outcome) for call in first] == [
        ("azure-openai", "failed"),
        ("openai", "failed"),
    ]
    assert [(call.provider, call.outcome) for call in second] == [("azure-openai", "succeeded")]
    notices = [
        warning for warning in Job.objects.get().warnings if warning["kind"] == "text_on_openai"
    ]
    assert len(notices) == 1
    assert "scene 1" in notices[0]["text"]


def test_the_real_adapter_resends_the_identical_turn_with_openais_own_key(
    httpserver: HTTPServer,
    settings: Settings,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    settings.OPENAI_API_KEY = "azure-test-key"
    settings.OPENAI_BASE_URL = httpserver.url_for("/azure/v1/")
    settings.OPENAI_PICTURES_KEY = "openai-test-key"
    settings.OPENAI_PICTURES_BASE_URL = httpserver.url_for("/v1/")
    httpserver.expect_oneshot_request(
        "/azure/v1/responses", method="POST", headers={"Authorization": "Bearer azure-test-key"}
    ).respond_with_json(
        {"error": {"code": "content_filter", "message": "text blocked"}}, status=400
    )
    httpserver.expect_oneshot_request(
        "/v1/responses", method="POST", headers={"Authorization": "Bearer openai-test-key"}
    ).respond_with_json(openai_turn("What product shall we make an ad for?"))

    with use_model(OpenAIText()):
        say("Hello")

    assert chat(api, session_id)[-1] == ("agent", "What product shall we make an ad for?")
    requests = [request for request, _ in httpserver.log]
    assert len(requests) == 2
    assert requests[0].get_json() == requests[1].get_json()
