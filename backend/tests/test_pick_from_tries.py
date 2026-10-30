"""The shop owner picks from every try when the fixing agent gives up on a B-roll clip the
clip judge failed (door 1; #180 generalised to any number of tries): the last try stays in
the ad (Julian, 2026-10-23), and the chat shows the first clip and every try, each with the
judge's problem, the agent's pick marked and why it gave up. The ad doesn't wait. The shop
owner swaps a try in from the scene's card (tests/test_card_versions.py), for nothing, and the
producer can with keep_try. The producer's model, the scene models, the video model and
the judge are faked at the gateway, and the agent by tests/agent_tries.py."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from chat import sent as sent_lists
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from jobs import notices, work
from jobs.models import BROLL_FIELDS, AgentFix, Job, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice

from .agent_tries import agent_films
from .conftest import (
    HeldSteps,
    paid_for,
    results_of,
)
from .test_producer_auto_fix import (
    FAILED,
    FLOATS,
    REMADE,
    REMADE_AGAIN,
    SPINS,
    clip_of_scene_2,
    failing,
    old_remakes,
)
from .test_producer_broll import (
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_picture_then_clip import ads

pytestmark = pytest.mark.django_db(transaction=True)

GAVE_UP = "Each try keeps the mug turning; I can't film it holding still."


def every_try_fails(fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scene 2's first clip fails on the teapot floating, and each of the agent's two tries
    on the mug spinning; the agent gives up, picking its first try."""
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(SPINS),
        failing(SPINS),
    )
    agent_films(monkeypatch, REMADE, REMADE_AGAIN, reason=GAVE_UP, picks=1)


@pytest.fixture
def failed_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> ProducedItem:
    """The first ad, made through the chat: the agent gave up on scene 2's clip, so its last
    try is in the ad."""
    every_try_fails(fake_model, monkeypatch)
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def tries() -> list[ProducedItem]:
    """Scene 2's first clip and the agent's 2 tries."""
    return list(ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("id")[:3])


def labels(number: int) -> list[str]:
    return [version.label for version in work.versions(Scene.objects.get(number=number))]


def current_clip(number: int) -> ProducedItem:
    clip = work.versions(Scene.objects.get(number=number))[-1].clip
    assert clip is not None
    return clip


# --- The last try stays in the ad, every try a version ----------------------------------------


def test_every_try_is_a_version_and_the_last_try_is_in_the_ad(failed_ad: ProducedItem) -> None:
    assert labels(2) == ["v1 first version (first clip)", "v2 try 1", "v3 try 2"]
    scene = Scene.objects.get(number=2)
    assert (scene.shows, scene.broll_kind, scene.status) == (SHOWS, "showcase", "finished")
    # The ad doesn't wait: it is made with the last try, and nothing is made again.
    _, _, try_2 = tries()
    assert current_clip(2) == try_2
    assert [cut["clip"] for cut in failed_ad.cuts if cut["scene"] == 2] == [try_2.pk]
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 3
    assert AgentFix.objects.get().kept == try_2


def test_the_producer_is_told_the_agent_gave_up_and_the_last_try_is_in_the_ad(
    failed_ad: ProducedItem,
) -> None:
    step = SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id").first()
    assert step is not None
    assert (
        "and the fixing agent gave up after 2 tries, so the last try is in the ad. Scene 2's card "
        "shows the shop owner every try, so they can put one in the ad from it, for nothing. "
        "Scene 2 is finished."
    ) in step.result
    assert "talking scene" not in step.result


def the_tries_notice(api: APIClient, session_id: str) -> dict[str, Any]:
    """The chat's notice showing scene 2's tries, as the browser reads it."""
    (notice,) = [
        message
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["tries"] is not None
    ]
    shown: dict[str, Any] = notice
    return shown


def test_the_chat_shows_every_try_with_the_judges_problem_and_the_agents_pick_and_reason(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    notice = the_tries_notice(api, session_id)
    first, try_1, try_2 = tries()
    assert (notice["role"], notice["level"]) == ("notice", "info")
    assert notice["text"] == (
        "None of the 3 tries at scene 2's clip passed my check, so the last try is in the ad. "
        "Watch them, and pick one on scene 2's card to use it instead."
    )
    assert notice["attachments"] == []
    # The notice ends the clip's step in the chat.
    assert notice["step"] == {"scene": 2, "kind": "clip", "state": "made"}
    shown_first = notice["tries"]["clips"][0]["problem"]
    assert shown_first == FLOATS
    assert notice["tries"] == {
        "scene": 2,
        "in_ad": try_2.pk,
        "on_offer": True,
        "picked": try_1.pk,
        "reason": GAVE_UP,
        "clips": [
            {
                "clip": clip.pk,
                "name": name,
                "version": version,
                "url": file_store.url(clip.file),
                "problem": problem,
                "sent": sent_lists.shown(clip),
            }
            for clip, name, version, problem in (
                (first, "First clip", "v1 first version (first clip)", shown_first),
                (try_1, "Try 1", "v2 try 1", SPINS),
                (try_2, "Try 2", "v3 try 2", SPINS),
            )
        ],
    }
    # Each try carries what it was sent, so the scene's card shows the one kept in the ad.
    assert try_2.sent is not None
    assert notice["tries"]["clips"][2]["sent"]["prompt"] == try_2.sent["prompt"]
    # No B-roll clip of scene 2 is shown in the chat but in the notice.
    tried = {clip.file for clip in tries()}
    assert not Attachment.objects.filter(kind="video", scene=2, file__in=tried).exists()


# --- A try swapped in from the scene's card ---------------------------------------------------


def use_on_card(
    api: APIClient, session_id: str, fake_model: FakeModel, version: int, scene: int = 2
) -> Any:
    """The shop owner taps "Use this in the ad" on scene `scene`'s v`version`: code swaps it
    in and wakes the producer, which tells them."""
    fake_model.respond("produce", turn(says="It's in the ad."))
    return api.post(
        f"/api/sessions/{session_id}/scenes/{scene}/use/", {"version": version}, format="json"
    )


def test_a_try_swapped_in_from_the_card_is_in_the_ad_for_nothing_and_the_notice_says_so(
    failed_ad: ProducedItem, api: APIClient, session_id: str, fake_model: FakeModel
) -> None:
    _, try_1, _ = tries()
    paid = paid_for()

    sent = use_on_card(api, session_id, fake_model, 2)

    assert sent.status_code == 200, sent.json()
    scene = Scene.objects.get(number=2)
    assert (scene.shows, scene.broll_kind) == (SHOWS, "showcase")
    assert labels(2)[-1] == "v4 back to v2"
    assert current_clip(2).file == try_1.file
    assert paid_for() == paid
    # No producer step: it calls no tool for the swap.
    assert results_of("keep_try") == []
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (try_1.pk, True)
    (ad,) = [made for made in ads() if made != failed_ad]
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_a_card_swap_while_the_scene_is_being_made_is_refused_until_its_ready(
    failed_ad: ProducedItem, api: APIClient, session_id: str, fake_model: FakeModel
) -> None:
    # Its clip is being made again (it has no starting picture: spec #210 point 19).
    making = SceneStep.objects.filter(kind="clip", scene__number=2).last()
    assert making is not None
    SceneStep.objects.create(
        scene=making.scene, kind=making.kind, tool_call=making.tool_call, line=making.line
    )

    sent = use_on_card(api, session_id, fake_model, 2)

    assert (sent.status_code, sent.json()) == (
        409,
        {"detail": "Scene 2's picture or clip is being made right now. Try again once it's ready."},
    )
    assert labels(2)[-1] == "v3 try 2"


def test_after_the_scenes_line_changed_its_tries_cant_be_swapped_in(
    failed_ad: ProducedItem, api: APIClient, session_id: str, fake_model: FakeModel
) -> None:
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    sent = use_on_card(api, session_id, fake_model, 2)

    assert (sent.status_code, sent.json()) == (
        409,
        {"detail": "v2 was made for an older line of scene 2, so it can't go in the ad for free."},
    )
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (None, False)


SLOWER = "Pour the tea slower."


# --- A fix's clip the agent gave up on keeps the clip from before -----------------------------


@pytest.fixture
def made_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> ProducedItem:
    """The first ad, made through the chat, every clip passed by the judge."""
    from .test_producer_broll import assemble_through_the_chat

    return assemble_through_the_chat(fake_model, steps, say)


def test_an_old_jobs_remake_shown_before_the_fixing_agent_swaps_in_from_the_card(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    step = clip_of_scene_2(fake_model, steps, say)
    made = old_remakes(FAILED, FAILED)
    post_notice(
        step.scene.job, notices.TRIES_TO_PICK, "Watch them.", Message.Level.INFO, tries_of=step.pk
    )

    assert [shown.clip for shown in work.tries_shown(step).tries] == made
    number = next(
        version.number
        for version in work.versions(Scene.objects.get(number=2))
        if version.clip == made[1]
    )
    sent = use_on_card(api, session_id, fake_model, number)

    assert sent.status_code == 200, sent.json()
    assert current_clip(2).file == made[1].file


# --- A give-up from before 2026-10-23, said to camera, still swaps -------------------------------


def said_to_camera_as_before() -> None:
    """Have scene 2's give-up stand as one from before 2026-10-23 left it: the scene said to
    camera, nothing kept, and what saying it changed kept to put back."""
    scene = Scene.objects.get(number=2)
    after = Scene.objects.get(number=3)
    camera_before = {
        "shows": scene.shows,
        **{field: getattr(scene, field) for field in BROLL_FIELDS},
        "shortened_from": scene.shortened_from,
        "second_state": scene.second_state,
        "next_second_state": after.second_state,
    }
    scene.change_line(scene.line, shows="")
    scene.save()
    AgentFix.objects.update(said_to_camera=True, kept=None, camera_before=camera_before)


def test_a_card_swap_on_a_give_up_said_to_camera_before_shows_the_product_again(
    failed_ad: ProducedItem, api: APIClient, session_id: str, fake_model: FakeModel
) -> None:
    _, try_1, _ = tries()
    said_to_camera_as_before()
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (None, True)

    sent = use_on_card(api, session_id, fake_model, 2)

    assert sent.status_code == 200, sent.json()
    scene = Scene.objects.get(number=2)
    assert (scene.shows, scene.broll_kind) == (SHOWS, "showcase")
    assert current_clip(2).file == try_1.file


# --- The producer keeps a try: keep_try (spec #247 point 5, ticket #259) -------------------------


def test_the_producer_keeps_a_try_for_nothing_and_assembles_the_ad(
    failed_ad: ProducedItem, fake_model: FakeModel, say: Callable[..., None]
) -> None:
    _, try_1, _ = tries()
    paid = paid_for()

    calling(fake_model, say, ("keep_try", {"scene": 2, "try_number": 1}))

    (answer,) = results_of("keep_try")
    assert answer.startswith(
        "Scene 2's try 1 is in the ad now, as v4 back to v2: nothing was made or paid for. The "
        "ad was assembled again with it"
    )
    assert current_clip(2).file == try_1.file
    assert paid_for() == paid
    (ad,) = [made for made in ads() if made != failed_ad]
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [current_clip(2).pk]

    # The one in the ad, a try that isn't there and a scene with no tries are refused.
    calling(fake_model, say, ("keep_try", {"scene": 2, "try_number": 1}))
    calling(fake_model, say, ("keep_try", {"scene": 2, "try_number": 5}))
    calling(fake_model, say, ("keep_try", {"scene": 1, "try_number": 1}))
    assert results_of("keep_try")[1:] == [
        f"Refused: {work.TRY_IN_THE_AD} Nothing was done.",
        "Refused: scene 2's tries are its first clip, try 1 and try 2, not 5. Nothing was done.",
        "Refused: scene 1 has no tries to pick from: there are tries only of a clip the clip "
        "judge failed and the fixing agent filmed again. Use use_version for its versions. "
        "Nothing was done.",
    ]
    assert len(ads()) == 2
