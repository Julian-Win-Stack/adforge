"""Clips landing after an owner change stay versions, without finishing stale scenes."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Scene, SceneAgentRun

from .conftest import HeldSteps, handoffs, recorded, results_of
from .scene_script import script_scene_agents
from .test_pick_from_tries import failed_ad  # noqa: F401
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["place", "person"])
def test_an_owner_change_while_filming_reaches_the_agent_and_the_old_clip_cannot_finish_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    change: str,
) -> None:
    number = 3 if change == "place" else 1
    words = "Use a kitchen." if change == "place" else "Make her older."
    tool = (
        ("set_place", {"scene": number, "new_place": "A sunny kitchen."})
        if change == "place"
        else ("remake_person", {"remake": "look", "looks": "A woman in her sixties."})
    )

    def change_while_filming() -> dict[str, str]:
        fake_model.respond("produce", turn(calls=[tool]), turn(says="I passed the change on."))
        say(words)
        return {"state": "working"}

    script_scene_agents(fake_model)
    fake_model.respond("collect_clip", change_while_filming)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": number, "instruction": "Show the mug.", "quotes": ["Go on"]},
        ),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="Done."))
    steps.run_held()
    scene = Scene.objects.get(number=number)
    assert scene.status != Scene.Status.FINISHED
    assert scene.produced.filter(kind="clip").exists(), results_of("film")
    assert SceneAgentRun.objects.filter(instruction__icontains=change).exists()
    assert words in str(handoffs("scene_agent")[-1])
    producer = str(handoffs("produce")[-1]).lower()
    assert f"scene {number}" in producer and "needs filming again" in producer
    assert change in producer
    assert not any("unexpected error stopped" in text for _, text in recorded(api, session_id))


def test_a_stale_finished_scene_cannot_stop_a_card_swap_or_later_owner_message(
    failed_ad: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from agents.models import SystemNote

    from .conftest import chat
    from .test_pick_from_tries import use_on_card

    # Reproduce an already-bad finished row, before the landing guard was installed.
    Scene.objects.filter(number=1).update(line="The owner changed this line.")
    response = use_on_card(api, session_id, fake_model, 2)
    assert response.status_code == 200
    note = SystemNote.objects.get(kind=SystemNote.Kind.VERSION_USED)
    assert note.assembled
    assert "scene 1" in note.text and "needs filming again" in note.text
    fake_model.respond(
        "produce", turn(calls=[("assemble_ad", {})]), turn(says="Scene 1 needs filming again.")
    )
    say("Can you finish the ad?")
    assert "scene 1" in results_of("assemble_ad")[-1]
    assert "Scene 1 needs filming again." in str(chat(api, session_id))
    assert "I had to stop" not in str(chat(api, session_id))
    assert not any("unexpected error stopped" in text for _, text in recorded(api, session_id))


@pytest.mark.parametrize("outcome", ["pass", "give_up"])
def test_a_clip_landing_after_an_external_place_change_does_not_finish_the_scene(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    outcome: str,
) -> None:
    from jobs import work

    from .test_producer_auto_fix import failing

    def concurrent_change() -> dict[str, str]:
        # A place change committed by another worker while this clip was at the provider.
        work.new_scene_place(Scene.objects.get(number=2), "room", "A sunny kitchen.")
        return {"state": "working"}

    script_scene_agents(fake_model)
    fake_model.respond("collect_clip", concurrent_change)
    if outcome == "give_up":
        fake_model.respond("check_clip_product", failing("The mug has an extra handle."))
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the mug.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    scene = Scene.objects.get(number=2)
    assert scene.status == Scene.Status.PLANNED
    assert scene.produced.filter(kind="clip").exists()
    producer = str(handoffs("produce")[-1]).lower()
    assert "scene 2 needs filming again" in producer
    assert "place changed" in producer
    assert not any("unexpected error stopped" in text for _, text in recorded(api, session_id))


def test_legacy_talking_snapshots_stay_usable_until_the_presenter_really_changes(
    failed_ad: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from agents.models import ToolCall
    from jobs.models import ProducedItem, SceneStep

    # Persisted talking plans leave the B-roll face label blank; old snapshots kept no
    # portrait. The existing original file still identifies when its presenter was made.
    SceneStep.objects.filter(scene__number=1).update(portrait=None)
    before = ProducedItem.objects.filter(kind="finished_ad").count()
    calling(fake_model, say, ("assemble_ad", {}))
    assert ProducedItem.objects.filter(kind="finished_ad").count() == before + 1
    assert not results_of("assemble_ad")[-1].startswith("Refused:")
    cards = api.get(f"/api/sessions/{session_id}/scenes/").json()
    assert any(version["in_ad"] for version in cards[0]["versions"])
    fake_model.respond(
        "produce",
        turn(calls=[("remake_person", {"remake": "look", "looks": "A woman in her sixties."})]),
        turn(says="The new presenter needs new clips."),
    )
    say("Make her older.")
    assert "stale" in ToolCall.objects.filter(tool="remake_person").latest("pk").result
    assert Scene.objects.get(number=1).status == Scene.Status.PLANNED
    cards = api.get(f"/api/sessions/{session_id}/scenes/").json()
    assert not any(version["in_ad"] or version["free"] for version in cards[0]["versions"])
    assert not any("unexpected error stopped" in text for _, text in recorded(api, session_id))
