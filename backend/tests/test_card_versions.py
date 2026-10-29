"""The clip version picker on the scene cards (Julian 2026-10-29 22:16): each scene's card shows
every clip it was shown, tries included, in one numbering, with the clip judge's verdict, the
one in the ad and whether each can go in the ad for nothing. A tap of "Use this in the ad"
swaps it in by code, with no producer step: the producer is told by a system note, and the ad
is assembled again by itself once one was shown. Undo is the same swap back. The producer's
model, the scene models, the video model and the judge are faked at the gateway, and the
fixing agent by tests/agent_tries.py."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import SystemNote, ToolCall
from chat import sent as sent_lists
from chat.models import Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import AgentFix, OwnerQuestion, ProducedItem, Scene

from .conftest import paid_for
from .test_pick_from_tries import (
    current_clip,
    failed_ad,  # noqa: F401 (a fixture)
    labels,
    made_ad,  # noqa: F401 (a fixture)
    tries,
    use_on_card,
)
from .test_producer_auto_fix import FLOATS, SPINS
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import ads

pytestmark = pytest.mark.django_db(transaction=True)


def cards(api: APIClient, session_id: str) -> dict[int, list[dict[str, Any]]]:
    """Each scene's clips as its card reads them, by scene."""
    response = api.get(f"/api/sessions/{session_id}/scenes/")
    assert response.status_code == 200, response.json()
    return {card["scene"]: card["versions"] for card in response.json()}


def shown(api: APIClient, session_id: str, scene: int = 2) -> list[tuple[int, str, bool, bool]]:
    """Scene `scene`'s card as (number, verdict, in the ad, free) per clip."""
    return [
        (each["number"], each["verdict"], each["in_ad"], each["free"])
        for each in cards(api, session_id)[scene]
    ]


def test_the_card_shows_every_try_in_one_numbering_with_the_judges_verdict_and_the_one_in_the_ad(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    first, try_1, try_2 = tries()

    scene_2 = cards(api, session_id)[2]

    assert scene_2 == [
        {
            "number": number,
            "label": label,
            "url": file_store.url(clip.file),
            "picture": None,
            "verdict": "fail",
            "problem": problem,
            "in_ad": clip == try_2,
            "free": clip != try_2,
            "sent": sent_lists.shown(clip),
        }
        for number, label, clip, problem in (
            (1, "v1 first version (first clip)", first, FLOATS),
            (2, "v2 try 1", try_1, SPINS),
            (3, "v3 try 2", try_2, SPINS),
        )
    ]


def test_a_version_swapped_in_from_the_card_is_in_the_ad_for_nothing_and_the_ad_is_assembled_again(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    _, try_1, _ = tries()
    paid = paid_for()

    sent = use_on_card(api, session_id, fake_model, 2)

    assert sent.status_code == 200, sent.json()
    assert current_clip(2).file == try_1.file
    # The card keeps its numbering: the clip brought back is v2, not a v4 of its own.
    assert labels(2)[-1] == "v4 back to v2"
    assert shown(api, session_id) == [
        (1, "fail", False, True),
        (2, "fail", True, False),
        (3, "fail", False, True),
    ]
    assert [(each["number"], each["in_ad"]) for each in sent.json()["versions"]] == [
        (1, False),
        (2, True),
        (3, False),
    ]
    # Nothing made, judged or paid for, and the ad is assembled again with it by code.
    assert paid_for() == paid
    (again,) = [ad for ad in ads() if ad != failed_ad]
    assert [cut["clip"] for cut in again.cuts if cut["scene"] == 2] == [current_clip(2).pk]
    # The producer is told by the app's note, and takes no step for the swap.
    (note,) = SystemNote.objects.filter(kind=SystemNote.Kind.VERSION_USED)
    assert note.read_at is not None
    assert note.text.startswith(
        "The shop owner put scene 2's v2 in the ad from the scene's card, so it is the scene's "
        "clip now, as v4 back to v2: nothing was made, judged or paid for. It is done: don't "
        "swap it again. The ad was assembled again with it (version 2,"
    )
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    given = [getattr(each, "text", "") for each in produced[-1].handoff.conversation]
    assert f"From the app, not the shop owner: {note.text}" in given


def test_undo_swaps_the_version_before_back_in_for_nothing(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    _, _, try_2 = tries()
    use_on_card(api, session_id, fake_model, 2)

    undone = use_on_card(api, session_id, fake_model, 3)

    assert undone.status_code == 200, undone.json()
    assert current_clip(2).file == try_2.file
    assert shown(api, session_id) == [
        (1, "fail", False, True),
        (2, "fail", False, True),
        (3, "fail", True, False),
    ]
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_the_one_in_the_ad_and_a_version_the_card_hasnt_got_are_refused(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    in_ad = use_on_card(api, session_id, fake_model, 3)
    missing = use_on_card(api, session_id, fake_model, 9)
    no_scene = use_on_card(api, session_id, fake_model, 1, scene=7)

    assert (in_ad.status_code, in_ad.json()) == (409, {"detail": "v3 is already in the ad."})
    assert (missing.status_code, missing.json()) == (
        409,
        {"detail": "Scene 2 has no v9 to use."},
    )
    assert (no_scene.status_code, no_scene.json()) == (
        409,
        {"detail": "This ad has no scene 7."},
    )
    assert not SystemNote.objects.filter(kind=SystemNote.Kind.VERSION_USED).exists()


def test_a_version_made_for_an_older_line_shows_but_cant_be_swapped_in(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    scene = Scene.objects.get(number=2)
    scene.change_line("Stoneware that keeps your tea hot.")
    scene.save(update_fields=["line", "shortened_from", "status"])

    # No clip says the new line yet, so none is in the ad and none is free.
    assert shown(api, session_id) == [
        (1, "fail", False, False),
        (2, "fail", False, False),
        (3, "fail", False, False),
    ]
    refused = use_on_card(api, session_id, fake_model, 1)
    assert refused.status_code == 409


def test_a_swap_is_refused_while_another_session_works(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    Session.objects.create(producer_running=True)

    refused = use_on_card(api, session_id, fake_model, 2)

    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "Another ad is being made right now. Please try again once it's finished."},
    )
    assert labels(2)[-1] == "v3 try 2"


def test_a_swap_before_the_first_ad_tells_the_producer_and_assembles_nothing(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    ProducedItem.objects.filter(kind="finished_ad").delete()

    sent = use_on_card(api, session_id, fake_model, 1)

    assert sent.status_code == 200, sent.json()
    assert ads() == []
    (note,) = SystemNote.objects.filter(kind=SystemNote.Kind.VERSION_USED)
    assert note.text.endswith("It is done: don't swap it again.")
    assert note.read_at is not None


def test_a_clip_the_judge_passed_or_never_judged_shows_so_with_its_first_frame(
    made_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    broll = ProducedItem.objects.get(kind="clip", scene__number=2)
    talking = ProducedItem.objects.get(kind="clip", scene__number=1)
    assert broll.picture is None and talking.picture is not None

    shown_cards = cards(api, session_id)

    (version,) = shown_cards[2]
    assert (version["verdict"], version["problem"], version["in_ad"], version["free"]) == (
        "pass",
        "",
        True,
        False,
    )
    # A scene showing the product is made from no starting picture.
    assert version["picture"] is None
    # A talking clip, never judged, starts from its starting picture.
    (said,) = shown_cards[1]
    assert (said["verdict"], said["picture"]) == (
        "not_checked",
        file_store.url(talking.picture.file),
    )


def test_a_swap_withdraws_the_fixing_agents_open_question_about_the_scene(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
) -> None:
    OwnerQuestion.objects.create(
        scene=Scene.objects.get(number=2),
        agent_fix=AgentFix.objects.get(),
        tool_call=ToolCall.objects.order_by("id")[0],
        question="Have you a video of the mug standing still?",
        wants=OwnerQuestion.Wants.VIDEO,
    )

    use_on_card(api, session_id, fake_model, 2)

    assert not OwnerQuestion.objects.exists()
    assert Message.objects.filter(
        role="notice",
        text__startswith="The fixing agent's question about scene 2's clip is withdrawn",
    ).exists()


def test_a_swap_while_the_producer_works_is_read_by_it_once_it_replies(
    failed_ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    def swap() -> None:
        sent = api.post(f"/api/sessions/{session_id}/scenes/2/use/", {"version": 2}, format="json")
        assert sent.status_code == 200, sent.json()

    fake_model.respond(
        "produce",
        meanwhile(swap, turn(says="It's looking good.")),
        turn(says="v2 is in the ad now."),
    )

    say("How does it look?")

    # One producer: the swap's note is read by the one working, on a turn of its own.
    (note,) = SystemNote.objects.filter(kind=SystemNote.Kind.VERSION_USED)
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    given = [getattr(each, "text", "") for each in produced[-1].handoff.conversation]
    assert f"From the app, not the shop owner: {note.text}" in given
    assert "The ad was assembled again with it" in note.text
    assert not Session.objects.get(pk=session_id).producer_running
