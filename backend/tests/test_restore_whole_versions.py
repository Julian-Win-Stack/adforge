"""Whole versions return through the card and chat without another paid check."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import AgentFixTry, Scene

from .conftest import HeldSteps, facts_ok, paid_for, results_of
from .test_card_versions import cards
from .test_pick_from_tries import current_clip, use_on_card
from .test_producer_broll import checked, run  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_producer_picture_then_clip import ads
from .test_producer_plan_edits import owner_says

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("via", ["card", "chat"])
@pytest.mark.parametrize("change_line", [False, True], ids=["action", "line-and-action"])
def test_swapping_restores_the_clip_and_scene_without_paying_again(
    shown: object,  # noqa: F811
    steps: HeldSteps,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
    via: str,
    change_line: bool,
) -> None:
    scene = Scene.objects.get(number=2)
    original = (scene.line, scene.shows, scene.result, scene.broll_kind)
    old = cards(api, session_id)[2][0]
    old_try = AgentFixTry.objects.get(clip_id=old["clip"])
    original_owner_words = list(scene.owner_words)
    if change_line:
        # Persisted versions from before owner-word provenance have no source list.
        old_try.scene_row.pop("owner_words", None)
        old_try.save(update_fields=["scene_row"])
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fields = {
        "line": scene.line,
        "shows": "The mug stands on a shelf.",
        "usage": "",
        "result": "",
        "broll_kind": "showcase",
        "person_shown": "no face",
    }
    if change_line:
        fields["line"] = "Hand-thrown and dishwasher safe, a mug for your daily tea at home."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", fields)]),
        *([] if change_line else [turn(calls=[("change_scene", fields)])]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    note = "Show the mug on a shelf."
    if change_line:
        note += f" Say exactly: “{fields['line']}”"
    owner_says(
        fake_model,
        say,
        note,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": note, "quotes": [note], "owner_asked": True}]},
        ),
    )
    run(fake_model, steps)
    scene.refresh_from_db()
    assert scene.shows == fields["shows"], results_of("change_scene")
    new_owner_words = list(scene.owner_words)
    if change_line:
        assert fields["line"] in new_owner_words
    paid = paid_for()

    if via == "card":
        response = use_on_card(api, session_id, fake_model, 1)
        assert response.status_code == 200, response.json()
    else:
        owner_says(
            fake_model, say, "Put the old one back", ("use_version", {"scene": 2, "version": 1})
        )

    scene.refresh_from_db()
    assert (scene.line, scene.shows, scene.result, scene.broll_kind) == original
    assert current_clip(2).pk != old["clip"]
    assert cards(api, session_id)[2][0]["in_ad"]
    assert paid_for() == paid
    assert scene.fact_checked
    assert scene.owner_words == original_owner_words
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]
    if change_line:
        owner_says(
            fake_model, say, "Use the newer one again", ("use_version", {"scene": 2, "version": 2})
        )
        scene.refresh_from_db()
        assert scene.line == fields["line"]
        assert scene.owner_words == new_owner_words
        assert paid_for() == paid


def test_old_voice_version_quotes_refilm_before_restoring_anything(
    shown: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from agents.models import ToolCall
    from chat.models import Message

    proposal = {"remake": "voice", "voice": "A soft, measured woman's voice."}
    owner_says(fake_model, say, "Change her voice.", ("remake_person", proposal))
    quote = ToolCall.objects.filter(tool="remake_person").latest("pk")
    owner_says(
        fake_model,
        say,
        "Yes, change the voice",
        (
            "remake_person",
            {**proposal, "cost_quote": quote.pk, "confirmation": "Yes, change the voice"},
        ),
    )
    before = current_clip(2)
    paid = paid_for()
    owner_says(fake_model, say, "Put the old one back", ("use_version", {"scene": 2, "version": 1}))
    assert paid_for() == paid
    assert current_clip(2) == before
    version = cards(api, session_id)[2][0]
    assert not version["free"]
    assert "old voice" in version["refilm"] and "$" in version["refilm"]
    notices = list(
        Message.objects.filter(role="agent", text__contains="old voice").values_list(
            "text", flat=True
        )
    )
    assert any("$" in notice and "refilm" in notice for notice in notices)
    refilm_quote = ToolCall.objects.filter(tool="use_version").latest("pk")
    owner_says(
        fake_model,
        say,
        "Yes, refilm that version",
        (
            "use_version",
            {
                "scene": 2,
                "version": 1,
                "cost_quote": refilm_quote.pk,
                "confirmation": "Yes, refilm that version",
            },
        ),
    )
    assert Scene.objects.get(number=2).status == Scene.Status.PLANNED
    assert paid_for() == paid


@pytest.mark.parametrize(
    "stored_portrait", [False, True], ids=["historical-empty-portrait", "stored-portrait"]
)
def test_a_talking_version_stays_free_until_its_presenter_changes(
    shown: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    stored_portrait: bool,
) -> None:
    from jobs.models import ProducedItem

    from .conftest import chat

    scene = Scene.objects.get(number=1)
    old = cards(api, session_id)[1][0]
    old_clip = ProducedItem.objects.get(pk=old["clip"])
    assert old_clip.step is not None
    old_clip.step.portrait = ProducedItem.objects.get(kind="portrait") if stored_portrait else None
    old_clip.step.save(update_fields=["portrait"])
    if not stored_portrait:
        filmed = AgentFixTry.objects.get(clip=old_clip)
        filmed.scene_row.pop("kind", None)
        filmed.scene_row.pop("kind_why", None)
        filmed.save(update_fields=["scene_row"])
    # The live kind can differ from this talking version's recorded kind. The presenter
    # check must classify the version being restored, not the row that will be replaced.
    Scene.objects.filter(pk=scene.pk).update(kind=Scene.Kind.BROLL)
    owner_says(
        fake_model, say, "Put scene 1's old one back", ("use_version", {"scene": 1, "version": 1})
    )
    assert "old presenter" not in str(chat(api, session_id))
    assert Scene.objects.get(number=1).kind == Scene.Kind.TALKING
    owner_says(
        fake_model,
        say,
        "Make her older.",
        ("remake_person", {"remake": "look", "looks": "A woman in her sixties."}),
    )
    paid = paid_for()
    before = current_clip(1)
    owner_says(
        fake_model, say, "Put scene 1's old one back", ("use_version", {"scene": 1, "version": 1})
    )
    version = cards(api, session_id)[1][0]
    assert not version["free"]
    assert "old presenter" in version["refilm"] and "$" in version["refilm"]
    assert current_clip(1) == before
    assert paid_for() == paid


def test_giving_up_a_legacy_run_clears_the_newer_scripts_owner_words(
    shown: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
) -> None:
    from gateway.types import Turn
    from jobs.models import SceneAgentRun

    scene = Scene.objects.get(number=2)
    original_line = scene.line
    line = "Hand-thrown and dishwasher safe, a mug for your daily tea at home."
    note = f"Say exactly: “{line}”"
    owner_says(
        fake_model,
        say,
        note,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": note,
                        "quotes": [note],
                        "owner_asked": True,
                    }
                ]
            },
        ),
    )
    run_record = SceneAgentRun.objects.filter(agent_fix__step__scene=scene).latest("pk")
    run_record.scene_before.pop("owner_words", None)
    run_record.save(update_fields=["scene_before"])
    fake_model.respond("fact_check", facts_ok(2))

    def give_up_after_the_new_line() -> Turn:
        assert Scene.objects.get(number=2).owner_words == [line]
        return turn(calls=[("give_up", {"reason": "Another product view is needed."})])

    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "change_scene",
                    {
                        "line": line,
                        "shows": scene.shows,
                        "usage": scene.usage,
                        "result": scene.result,
                        "broll_kind": scene.broll_kind,
                        "person_shown": scene.person_shown,
                    },
                )
            ]
        ),
        give_up_after_the_new_line,
    )
    run(fake_model, steps)
    scene.refresh_from_db()
    assert scene.line == original_line
    assert scene.owner_words == []
