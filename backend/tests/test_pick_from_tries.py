"""The shop owner picks from every try when the clip judge fails them all (#180): a B-roll
clip the judge failed, and both its remakes too, is shown in the chat with its remakes, each
with the judge's problem, while the app's pick is already in the ad. One tap swaps another
in, for nothing, and every try is a version of the scene. The producer's model, the scene
models, the video model and the judge are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from adforge import file_store
from agents import run_plan
from agents.tasks import wake_producer
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, turn
from jobs import work
from jobs.models import ProducedItem, Scene, SceneStep

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    HeldSteps,
    handoffs,
    motion_answer,
    not_a_fix,
    paid_for,
    producer_turns,
)
from .fixing import fixed
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_fix_wiring import decided, planning, plans, row, tap_yes
from .test_producer_auto_fix import FLOATS, REMADE, REMADE_AGAIN, SPINS, failing, rewrite
from .test_producer_broll import (
    TALKING_CHOICE,
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import ads

pytestmark = pytest.mark.django_db(transaction=True)


def every_try_fails(fake_model: FakeModel) -> None:
    """Scene 2's first clip fails 2 answers on the teapot floating, and each remake 3, on the
    mug spinning: the app picks the first clip, with the fewest failed answers."""
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS_TOO),
        CLIP_MOTION_OK,
        *[failing(SPINS)] * 3,
        *[failing(SPINS)] * 3,
    )
    fake_model.respond("remake_clip_prompt", rewrite(REMADE), rewrite(REMADE_AGAIN))


# The same fault, as the judge's second ask words it.
FLOATS_TOO = "From 1.2 s the teapot hangs in the air with nothing holding it."


@pytest.fixture
def failed_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> ProducedItem:
    """The first ad, made through the chat, scene 2's clip failed by the judge every try."""
    every_try_fails(fake_model)
    return assemble_through_the_chat(fake_model, steps, say)


def tries() -> list[ProducedItem]:
    """Scene 2's first clip and its 2 remakes."""
    return list(ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("id")[:3])


def labels(number: int) -> list[str]:
    return [version.label for version in work.versions(Scene.objects.get(number=number))]


# --- Every try is a version ----------------------------------------------------------------


def test_every_try_is_a_version_labelled_with_the_try_and_the_pick_is_the_newest(
    failed_ad: ProducedItem,
) -> None:
    assert labels(2) == [
        "v1 first version (first try)",
        "v2 first version (remake 1)",
        "v3 first version (remake 2)",
        "v4 back to v1",
    ]
    first, remake_1, remake_2 = tries()
    versions = work.versions(Scene.objects.get(number=2))
    assert [version.clip for version in versions[:3]] == [first, remake_1, remake_2]
    # The app's pick is the scene's clip, so the ad is made from it.
    assert versions[3].clip is not None and versions[3].clip.file == first.file
    assert [cut["clip"] for cut in failed_ad.cuts if cut["scene"] == 2] == [versions[3].clip.pk]


def test_a_remake_the_judge_passed_is_still_the_only_version(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond("remake_clip_prompt", rewrite(REMADE))

    assemble_through_the_chat(fake_model, steps, say)

    assert labels(2) == ["v1 first version"]


def test_a_scene_made_before_tries_were_shown_keeps_its_versions(
    failed_ad: ProducedItem,
) -> None:
    # As it was made before #180: the chat never showed its tries, and the app's pick was
    # brought back as the scene's first version.
    Message.objects.filter(tries_of__isnull=False).delete()
    ProducedItem.objects.filter(kind="clip", scene__number=2, back_to=1).update(
        back_to=None, change=""
    )

    assert labels(2) == ["v1 first version"]
    assert current_clip(2).file == tries()[0].file


def test_the_clip_shown_in_the_chat_is_labelled_as_the_version_gone_back_to(
    failed_ad: ProducedItem,
) -> None:
    (shown,) = Attachment.objects.filter(kind="video", scene=2, message__role=Message.Role.AGENT)
    assert (shown.file, shown.version) == (tries()[0].file, "v4 back to v1")


# --- The chat shows every try ----------------------------------------------------------------


def the_tries_notice(api: APIClient, session_id: str) -> dict[str, Any]:
    """The chat's notice showing scene 2's tries, as the browser reads it."""
    (notice,) = [
        message
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["tries"] is not None
    ]
    shown: dict[str, Any] = notice
    return shown


def test_the_chat_shows_every_try_with_the_judges_first_problem_and_the_one_in_the_ad(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    notice = the_tries_notice(api, session_id)
    first, remake_1, remake_2 = tries()
    assert (notice["role"], notice["level"]) == ("notice", "info")
    assert notice["text"] == (
        "The clip judge found a problem in all 3 tries at scene 2's clip. The app put the one "
        "with the fewest problems in the ad. Watch them, and tap another to use it instead."
    )
    assert notice["attachments"] == []
    # Only the first of the judge's answers, as they word the same fault: the asks are made at
    # once, so which of the two came first is whichever answered first.
    shown_first = notice["tries"]["clips"][0]["problem"]
    assert shown_first in (FLOATS, FLOATS_TOO)
    assert notice["tries"] == {
        "scene": 2,
        "in_ad": first.pk,
        "clips": [
            {
                "clip": first.pk,
                "name": "First try",
                "version": "v1 first version (first try)",
                "url": file_store.url(first.file),
                "problem": shown_first,
            },
            {
                "clip": remake_1.pk,
                "name": "Remake 1",
                "version": "v2 first version (remake 1)",
                "url": file_store.url(remake_1.file),
                "problem": SPINS,
            },
            {
                "clip": remake_2.pk,
                "name": "Remake 2",
                "version": "v3 first version (remake 2)",
                "url": file_store.url(remake_2.file),
                "problem": SPINS,
            },
        ],
    }
    # It comes right after the clip in the ad is shown.
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    at = next(i for i, message in enumerate(messages) if message["tries"] is not None)
    assert messages[at - 1]["attachments"][0]["version"] == "v4 back to v1"


def test_no_tries_are_shown_when_a_remake_passes(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond("remake_clip_prompt", rewrite(REMADE))

    assemble_through_the_chat(fake_model, steps, say)

    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert [message for message in shown if message["tries"] is not None] == []


def test_the_producer_is_told_the_chat_showed_every_try(failed_ad: ProducedItem) -> None:
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert "The clip kept is the first clip, the one with the fewest faults" in step.result
    assert (
        " The chat showed the shop owner all 3 tries, each with what the judge still sees, so "
        "they can tap another to use it instead, for nothing. Scene 2 is finished."
    ) in step.result


# --- One tap swaps a try in ------------------------------------------------------------------


def tap_use(api: APIClient, session_id: str, clip: ProducedItem) -> Any:
    """The shop owner taps "Use this one" under `clip`."""
    return api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "", "use_clip": clip.pk}, format="json"
    )


def current_clip(number: int) -> ProducedItem:
    clip = work.versions(Scene.objects.get(number=number))[-1].clip
    assert clip is not None
    return clip


def test_a_tap_swaps_a_try_in_for_nothing_and_assembles_the_ad_again(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, remake_1, _ = tries()
    paid, turns = paid_for(), producer_turns()

    sent = tap_use(api, session_id, remake_1)

    assert sent.status_code == 201, sent.json()
    tap = sent.json()["message"]
    assert (tap["role"], tap["text"], tap["use_clip"]) == (
        "user",
        "Use remake 1 for scene 2.",
        remake_1.pk,
    )
    # Going back to it, as a version of its own, with nothing made, judged or paid for.
    assert labels(2)[-1] == "v5 back to v2"
    assert current_clip(2).file == remake_1.file
    assert (paid_for(), producer_turns()) == (paid, turns)
    # The chat shows it as the clip in the ad, and the new ad made from it.
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    clips_shown = [
        attachment
        for message in shown
        for attachment in message["attachments"]
        if attachment["kind"] == "video" and attachment["scene"] == 2
    ]
    assert clips_shown[-1]["version"] == "v5 back to v2"
    assert the_tries_notice(api, session_id)["tries"]["in_ad"] == remake_1.pk
    (ad,) = [made for made in ads() if made != failed_ad]
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_a_tap_on_the_try_in_the_ad_is_refused(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    first, _, _ = tries()

    sent = tap_use(api, session_id, first)

    assert (sent.status_code, sent.json()) == (400, {"use_clip": "That try is already in the ad."})
    assert labels(2)[-1] == "v4 back to v1"


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
    first, remake_1, _ = tries()
    producer_busy(session_id)

    assert tap_use(api, session_id, remake_1).status_code == 201
    again = tap_use(api, session_id, remake_1)
    back = tap_use(api, session_id, first)

    assert (again.status_code, again.json()) == (
        400,
        {"use_clip": "That try is already in the ad."},
    )
    assert back.status_code == 201, back.json()
    producer_done(session_id)
    assert labels(2)[-2:] == ["v5 back to v2", "v6 back to v1"]
    assert not Message.objects.filter(role="notice", text__contains="couldn't use").exists()


def test_a_tap_refused_once_routed_says_so_in_red_and_changes_nothing(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, remake_1, _ = tries()
    producer_busy(session_id)
    assert tap_use(api, session_id, remake_1).status_code == 201
    # The line changes before the tap is routed: the tries no longer say it.
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    producer_done(session_id)

    assert current_clip(2).file == tries()[0].file
    told = Message.objects.filter(role="notice", level="problem").last()
    assert told is not None
    assert told.text == "Scene 2 couldn't use remake 1. Those tries aren't on offer any more."


def test_a_tap_while_the_scene_is_being_made_is_refused_until_its_ready(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, remake_1, _ = tries()
    # Its picture is being drawn again.
    drawn = SceneStep.objects.filter(kind="starting_picture", scene__number=2).get()
    SceneStep.objects.create(
        scene=drawn.scene, kind=drawn.kind, tool_call=drawn.tool_call, line=drawn.line
    )

    sent = tap_use(api, session_id, remake_1)

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


def test_the_decider_reads_what_the_owner_typed_after_a_tap_but_not_the_tap(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    _, remake_1, _ = tries()
    tap_use(api, session_id, remake_1)
    fake_model.respond("decide_fixes", not_a_fix())
    fake_model.respond("produce", turn(says="It shows the mug."))

    say("What does scene 2 show now?")

    read = [each for each in fake_model.turns_taken if each.purpose == "decide_fixes"][-1]
    said = [getattr(each, "text", "") for each in read.handoff.conversation]
    assert any("What does scene 2 show now?" in text for text in said)
    assert not any("Use remake 1" in text for text in said)


def test_typing_which_try_to_use_goes_back_to_it_for_nothing(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    _, remake_1, _ = tries()
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

    assert current_clip(2).file == remake_1.file
    assert labels(2)[-1] == "v5 back to v2"
    assert paid_for() == paid


# --- A note improves the clip in the ad -------------------------------------------------------

SLOWER = "Pour the tea slower."


def test_a_note_after_a_tap_has_the_rewrite_start_from_the_swapped_in_clips_movement(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    _, remake_1, _ = tries()
    tap_use(api, session_id, remake_1)

    fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE_AGAIN))
    steps.run_next()

    assert handoffs("rewrite_clip_prompt")[-1]["motion_prompt"] == REMADE


# --- The same screen after a note's clip -----------------------------------------------------


def a_new_clip_failed_every_try(
    fake_model: FakeModel, steps: HeldSteps, owner_change: dict[str, Any] = CLIP_FIXED
) -> SceneStep:
    """A plan's new clip for scene 2, said yes to and made: the judge fails it and both its
    remakes, and its fix check answers `owner_change`. Gives its step."""
    fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.answer_unscripted("check_clip_motion", motion_answer(failing(FLOATS), owner_change))
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.respond("remake_clip_prompt", rewrite(REMADE), rewrite(REMADE_AGAIN))
    steps.run_held()
    return SceneStep.objects.filter(kind="clip", scene__number=2).latest("id")


def test_a_notes_clip_failed_every_try_shows_every_try_not_a_line_saying_it_was_kept(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    step = a_new_clip_failed_every_try(fake_model, steps)

    notices = api.get(f"/api/sessions/{session_id}/messages/").json()
    shown = [each for each in notices if each["tries"] is not None][-1]
    assert [each["clip"] for each in shown["tries"]["clips"]] == [
        clip.pk for clip in work.clip_tries(step)
    ]
    assert shown["tries"]["in_ad"] == work.clip_tries(step)[-1].pk
    assert not any("though the judge still found a fault" in each["text"] for each in notices)


def test_a_tap_on_a_notes_try_held_on_its_fix_check_goes_ahead_with_it(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    step = a_new_clip_failed_every_try(fake_model, steps, not_made("It still pours fast."))
    (row,) = run_plan.held_on_a_check(Session.objects.get())
    first = work.clip_tries(step)[0]
    ads_before = len(ads())

    sent = tap_use(api, session_id, first)

    assert sent.status_code == 201, sent.json()
    assert current_clip(2).file == first.file
    assert run_plan.held_on_a_check(Session.objects.get()) == []
    row.refresh_from_db()
    assert row.landed_at is not None
    # The plan's new ad is assembled from the try chosen.
    assert len(ads()) == ads_before + 1
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_a_swap_while_another_scenes_fix_waits_says_when_the_ad_will_have_it(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    _, remake_1, _ = tries()
    # Scene 1's new picture failed its fix check, and waits for the owner's answer.
    fixed(1, "new_picture", change="The handle faces the camera.")
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    steps.run_held()
    assert run_plan.held_on_a_check(Session.objects.get()) != []

    tap_use(api, session_id, remake_1)

    assert current_clip(2).file == remake_1.file
    assert ads() == [failed_ad]
    told = Message.objects.filter(role="notice").last()
    assert told is not None
    assert (told.level, told.text) == (
        "info",
        "The ad will be put together again with scene 2's new clip once scene 1's fix is done.",
    )


def test_a_swap_is_kept_when_the_ad_cant_be_assembled_again_and_the_chat_says_so(
    failed_ad: ProducedItem,
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, remake_1, _ = tries()

    def broken(*_: object) -> None:
        raise RuntimeError("ffmpeg fell over")

    monkeypatch.setattr("agents.producer.assemble_ad", broken)

    tap_use(api, session_id, remake_1)

    assert current_clip(2).file == remake_1.file
    assert ads() == [failed_ad]
    told = Message.objects.filter(role="notice", level="problem").last()
    assert told is not None
    assert told.text.startswith("The ad couldn't be put together again with scene 2's new clip")


def test_a_tap_after_the_scenes_line_changed_is_refused_as_its_tries_no_longer_say_it(
    failed_ad: ProducedItem, api: APIClient, session_id: str
) -> None:
    _, remake_1, _ = tries()
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    sent = tap_use(api, session_id, remake_1)

    assert (sent.status_code, sent.json()) == (
        400,
        {"use_clip": "Those tries aren't on offer any more."},
    )
