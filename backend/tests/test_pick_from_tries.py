"""The shop owner picks from every try when the fixing agent gives up on a B-roll clip the
clip judge failed (door 1; #180 generalised to any number of tries): the last try stays in
the ad (Julian, 2026-10-23), and the chat shows the first clip and every try, each with the
judge's problem, the agent's pick marked and why it gave up. The ad doesn't wait. One tap swaps
a try in, for nothing. A fix's scene the agent gave up on keeps the clip it had before
instead. The producer's model, the scene models, the video model and
the judge are faked at the gateway, and the agent by tests/agent_tries.py."""

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from adforge import file_store
from agents.tasks import wake_producer
from chat import sent as sent_lists
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, turn
from jobs import work
from jobs.models import BROLL_FIELDS, AgentFix, Job, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice

from .agent_tries import agent_films
from .conftest import (
    CLIP_FIXED,
    HeldSteps,
    handoffs,
    motion_answer,
    not_a_fix,
    paid_for,
    producer_turns,
)
from .fixing import fixed
from .test_fix_wiring import decided, planning, plans, row, tap_yes
from .test_producer_auto_fix import (
    FAILED,
    FLOATS,
    REMADE,
    REMADE_AGAIN,
    SPINS,
    clip_of_scene_2,
    failing,
    old_remakes,
    rewrite,
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
        "and the fixing agent gave up after 2 tries, so the last try is in the ad. The chat "
        "showed the shop owner every try, so they can tap one to use it instead, for nothing. "
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
        "The clip judge found a problem in all 3 tries at scene 2's clip, so the last try is in "
        "the ad. Watch them, and tap one to use it instead."
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
    assert notice["tries"]["clips"][2]["sent"]["prompt"] == try_2.sent["prompt"]
    # No B-roll clip of scene 2 is shown in the chat but in the notice.
    tried = {clip.file for clip in tries()}
    assert not Attachment.objects.filter(kind="video", scene=2, file__in=tried).exists()


# --- One tap swaps a try in ------------------------------------------------------------------


def tap_use(api: APIClient, session_id: str, clip: ProducedItem) -> Any:
    """The shop owner taps "Use this one" under `clip`."""
    return api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "", "use_clip": clip.pk}, format="json"
    )


def test_a_tap_swaps_a_try_in_for_nothing_and_assembles_the_ad(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, try_1, _ = tries()
    paid, turns = paid_for(), producer_turns()

    sent = tap_use(api, session_id, try_1)

    assert sent.status_code == 201, sent.json()
    tap = sent.json()["message"]
    assert (tap["role"], tap["text"], tap["use_clip"]) == (
        "user",
        "Use try 1 for scene 2.",
        try_1.pk,
    )
    scene = Scene.objects.get(number=2)
    assert (scene.shows, scene.broll_kind) == (SHOWS, "showcase")
    assert labels(2)[-1] == "v4 back to v2"
    assert current_clip(2).file == try_1.file
    assert (paid_for(), producer_turns()) == (paid, turns)
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (try_1.pk, True)
    (ad,) = [made for made in ads() if made != failed_ad]
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_after_a_tap_another_try_can_be_swapped_in_and_the_one_in_the_ad_cant(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    first, try_1, _ = tries()
    tap_use(api, session_id, try_1)

    again = tap_use(api, session_id, try_1)
    back = tap_use(api, session_id, first)

    assert (again.status_code, again.json()) == (
        400,
        {"use_clip": "That try is already in the ad."},
    )
    assert back.status_code == 201, back.json()
    assert current_clip(2).file == first.file
    assert labels(2)[-1] == "v5 back to v1"


def producer_busy(session_id: str) -> None:
    """The producer is working in the chat, so a tap waits for it to be routed."""
    Session.objects.filter(pk=session_id).update(
        producer_running=True, producer_seen_at=timezone.now()
    )


def producer_done(session_id: str) -> None:
    """The producer finishes, and routes what was sent meanwhile."""
    Session.objects.filter(pk=session_id).update(producer_running=False)
    wake_producer(session_id)


def test_a_tap_while_another_waits_to_be_swapped_in_is_judged_by_the_one_waiting(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    first, try_1, _ = tries()
    producer_busy(session_id)

    assert tap_use(api, session_id, try_1).status_code == 201
    again = tap_use(api, session_id, try_1)
    back = tap_use(api, session_id, first)

    assert (again.status_code, again.json()) == (
        400,
        {"use_clip": "That try is already in the ad."},
    )
    assert back.status_code == 201, back.json()
    producer_done(session_id)
    assert labels(2)[-2:] == ["v4 back to v2", "v5 back to v1"]
    assert not Message.objects.filter(role="notice", text__contains="couldn't use").exists()


def test_a_tap_refused_once_routed_says_so_in_red_and_changes_nothing(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, try_1, try_2 = tries()
    producer_busy(session_id)
    assert tap_use(api, session_id, try_1).status_code == 201
    # The line changes before the tap is routed: the tries no longer say it.
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    producer_done(session_id)

    assert current_clip(2) == try_2
    assert labels(2)[-1] == "v3 try 2"
    told = Message.objects.filter(role="notice", level="problem").last()
    assert told is not None
    assert told.text == "Scene 2 couldn't use try 1. Those tries aren't on offer any more."


def test_a_tap_while_the_scene_is_being_made_is_refused_until_its_ready(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, try_1, _ = tries()
    # Its picture is being drawn again.
    drawn = SceneStep.objects.filter(kind="starting_picture", scene__number=2).last()
    assert drawn is not None
    SceneStep.objects.create(
        scene=drawn.scene, kind=drawn.kind, tool_call=drawn.tool_call, line=drawn.line
    )

    sent = tap_use(api, session_id, try_1)

    assert (sent.status_code, sent.json()) == (
        400,
        {
            "use_clip": "Scene 2's picture or clip is being made right now. Tap again once "
            "it's ready."
        },
    )


def test_a_tap_on_a_clip_that_isnt_a_try_shown_is_refused(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    talking = ProducedItem.objects.filter(kind="clip", scene__number=1).get()

    sent = tap_use(api, session_id, talking)

    assert sent.status_code == 400
    assert sent.json() == {"use_clip": "Those tries aren't on offer any more."}


def test_a_tap_after_the_scenes_line_changed_is_refused_as_its_tries_no_longer_say_it(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, try_1, _ = tries()
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    sent = tap_use(api, session_id, try_1)

    assert (sent.status_code, sent.json()) == (
        400,
        {"use_clip": "Those tries aren't on offer any more."},
    )
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (None, False)


def test_the_decider_reads_what_the_owner_typed_after_a_tap_but_not_the_tap(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    _, try_1, _ = tries()
    tap_use(api, session_id, try_1)
    fake_model.respond("decide_fixes", not_a_fix())
    fake_model.respond("produce", turn(says="It shows the mug."))

    say("What does scene 2 show now?")

    read = [each for each in fake_model.turns_taken if each.purpose == "decide_fixes"][-1]
    said = [getattr(each, "text", "") for each in read.handoff.conversation]
    assert any("What does scene 2 show now?" in text for text in said)
    assert not any("Use try 1" in text for text in said)


def test_typing_which_try_to_use_goes_back_to_it_for_nothing(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    _, try_1, _ = tries()
    paid = paid_for()
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_version", {"scene": 2, "version": 2}),
            decided(row(2, "go_back", go_back_to=2)),
        ),
    )
    say("Use the second one in scene 2.")

    tap_yes(api, session_id, plans()[-1])

    assert current_clip(2).file == try_1.file
    assert Scene.objects.get(number=2).shows == SHOWS
    assert labels(2)[-1] == "v4 back to v2"
    assert paid_for() == paid


def test_going_back_to_a_try_after_the_line_changed_keeps_the_newer_line(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    _, try_1, _ = tries()
    scene = Scene.objects.get(number=2)
    newer = "Stoneware that keeps your tea hot."
    scene.change_line(newer)
    scene.save(update_fields=["line", "shortened_from", "status"])
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_version", {"scene": 2, "version": 2}),
            decided(row(2, "go_back", go_back_to=2)),
        ),
    )
    say("Use the second one in scene 2.")

    tap_yes(api, session_id, plans()[-1])

    # The try's picture is brought back, the product shown again, with the newer line.
    scene.refresh_from_db()
    assert (scene.line, scene.shows) == (newer, SHOWS)
    top = work.versions(scene)[-1]
    assert top.picture is not None and top.picture.file == try_1.picture.file  # type: ignore[union-attr]
    assert top.clip is None


SLOWER = "Pour the tea slower."


def test_a_note_after_a_tap_has_the_rewrite_start_from_the_swapped_in_clips_movement(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    _, try_1, _ = tries()
    tap_use(api, session_id, try_1)

    fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE_AGAIN))
    steps.run_next()

    assert handoffs("rewrite_clip_prompt")[-1]["motion_prompt"] == REMADE


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


def a_new_clip_given_up_on(
    fake_model: FakeModel, steps: HeldSteps, monkeypatch: pytest.MonkeyPatch
) -> SceneStep:
    """A plan's new clip for scene 2, said yes to and made: the judge fails it and both the
    agent's tries, and the agent gives up. Gives its step."""
    fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.answer_unscripted("check_clip_motion", motion_answer(failing(FLOATS), CLIP_FIXED))
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    agent_films(monkeypatch, REMADE, REMADE_AGAIN, reason=GAVE_UP, picks=2)
    steps.run_held()
    return (
        SceneStep.objects.filter(kind="clip", scene__number=2)
        .exclude(tool_call__agent="fixing_agent")
        .latest("id")
    )


def test_a_fixs_clip_the_agent_gave_up_on_keeps_the_scenes_clip_from_before(
    made_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = current_clip(2)

    step = a_new_clip_given_up_on(fake_model, steps, monkeypatch)

    first, try_1, try_2 = work.clip_tries(step)
    # The scene keeps the clip it had, never one the judge failed, and still shows the mug.
    assert current_clip(2).file == before.file
    assert Scene.objects.get(number=2).shows == SHOWS
    assert AgentFix.objects.get(step=step).kept == current_clip(2)
    notices = api.get(f"/api/sessions/{session_id}/messages/").json()
    shown = [each for each in notices if each["tries"] is not None][-1]
    assert shown["text"] == (
        "The clip judge found a problem in all 3 tries at scene 2's clip, so scene 2 keeps the "
        "clip it had before. Watch them, and tap one to use it instead."
    )
    assert [each["clip"] for each in shown["tries"]["clips"]] == [first.pk, try_1.pk, try_2.pk]
    assert shown["tries"]["in_ad"] is None
    assert (shown["tries"]["on_offer"], shown["tries"]["picked"]) == (True, try_2.pk)
    assert not any("though the judge" in each["text"] for each in notices)
    # The plan's new ad is assembled with the clip from before.
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_a_tap_on_a_fixs_try_swaps_it_in_and_assembles_the_ad_again(
    made_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = a_new_clip_given_up_on(fake_model, steps, monkeypatch)
    _, _, try_2 = work.clip_tries(step)
    ads_before = len(ads())

    sent = tap_use(api, session_id, try_2)

    assert sent.status_code == 201, sent.json()
    assert current_clip(2).file == try_2.file
    assert len(ads()) == ads_before + 1
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_a_tap_on_an_old_jobs_remake_shown_before_the_fixing_agent_swaps_it_in(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    step = clip_of_scene_2(fake_model, steps, say)
    made = old_remakes(FAILED, FAILED)
    post_notice(step.scene.job, "Watch them.", Message.Level.INFO, tries_of=step.pk)

    assert [shown.clip for shown in work.tries_shown(step).tries] == made
    sent = tap_use(api, session_id, made[1])

    assert sent.status_code == 201, sent.json()
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


def test_a_tap_on_a_give_up_said_to_camera_before_shows_the_product_again(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, try_1, _ = tries()
    said_to_camera_as_before()
    notice = the_tries_notice(api, session_id)["tries"]
    assert (notice["in_ad"], notice["on_offer"]) == (None, True)

    sent = tap_use(api, session_id, try_1)

    assert sent.status_code == 201, sent.json()
    scene = Scene.objects.get(number=2)
    assert (scene.shows, scene.broll_kind) == (SHOWS, "showcase")
    assert current_clip(2).file == try_1.file
