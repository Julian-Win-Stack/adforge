"""The plan notice and its yes button (fix redesign, flow steps 6-7, decision 4), tested the
way the browser meets them: code posts a plan in the chat, the chat API reads it back with
the plan's fields, and the yes button sends a marked message that code can tell from a
typed one. Nothing here routes the yes or runs the plan (ticket 08)."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from jobs import fix_plans
from jobs.models import FixPlan, FixPlanScene, Job, Scene

pytestmark = pytest.mark.django_db(transaction=True)

NOTES = [{"seq": 1, "text": "Scene 2: make the toilet black"}]


def an_ad(scenes: int = 3) -> Job:
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/products/gel")
    for number in range(1, scenes + 1):
        Scene.objects.create(job=job, number=number, line=f"Line {number}.", shows="It.")
    return job


def a_plan(job: Job, question: str = "", cants: Sequence[fix_plans.PlanCant] = ()) -> FixPlan:
    return fix_plans.propose(
        job,
        NOTES,
        [
            FixPlanScene(
                scene=job.scenes.get(number=2),
                action=FixPlanScene.Action.NEW_PICTURE,
                change="The toilet is black.",
                estimate_usd=Decimal("0.55"),
                estimate_up_to_usd=Decimal("1.10"),
                photos=[
                    {
                        "photo": 1,
                        "file": "owner/toilet.jpg",
                        "thing": "black toilet",
                        "in_picture": True,
                        "during_action": False,
                    }
                ],
            ),
            FixPlanScene(
                scene=job.scenes.get(number=3),
                action=FixPlanScene.Action.NO_CHANGE,
                cant="The person's look can't be changed.",
            ),
        ],
        question=question,
        cants=cants,
    )


def chat_id(job: Job) -> str:
    assert job.session is not None
    return str(job.session.pk)


def conversation(api: APIClient, job: Job) -> list[dict[str, Any]]:
    response = api.get(f"/api/sessions/{chat_id(job)}/")
    assert response.status_code == 200
    said: list[dict[str, Any]] = response.json()["messages"]
    return said


@pytest.fixture
def send(api: APIClient, fake_model: FakeModel) -> Callable[..., Any]:
    """Send as the browser does; the producer it wakes says nothing back."""

    def sending(job: Job, body: dict[str, object]) -> Any:
        fake_model.respond("produce", turn())
        return api.post(f"/api/sessions/{chat_id(job)}/messages/", body, format="json")

    return sending


def test_a_plan_shown_in_the_chat_is_read_back_with_its_fields(api: APIClient) -> None:
    job = an_ad()
    plan = a_plan(
        job,
        question="Which toilet, the one in photo 1?",
        cants=[{"kind": "music", "text": "The music can't be changed."}],
    )

    shown = fix_plans.show(plan)

    assert shown is not None
    [said] = conversation(api, job)
    assert said["role"] == "notice"
    assert said["level"] == "info"
    assert said["text"]
    assert said["plan"] == {
        "id": plan.pk,
        "event": "shown",
        "notice": {
            "plan": plan.pk,
            "state": "proposed",
            "question": "Which toilet, the one in photo 1?",
            "estimate_usd": "0.550000",
            "estimate_up_to_usd": "1.100000",
            "cants": [{"kind": "music", "text": "The music can't be changed."}],
            "scenes": [
                {
                    "scene": 2,
                    "action": "new_picture",
                    "change": "The toilet is black.",
                    "line": "",
                    "go_back_to": None,
                    "cant": "",
                    "estimate_usd": "0.550000",
                    "estimate_up_to_usd": "1.100000",
                    "photos": [
                        {
                            "photo": 1,
                            "thing": "black toilet",
                            "in_picture": True,
                            "during_action": False,
                        }
                    ],
                },
                {
                    "scene": 3,
                    "action": "no_change",
                    "change": "",
                    "line": "",
                    "go_back_to": None,
                    "cant": "The person's look can't be changed.",
                    "estimate_usd": "0.000000",
                    "estimate_up_to_usd": "0.000000",
                    "photos": [],
                },
            ],
        },
    }
    # Its words say the same, for where the plan can't be drawn.
    for words in ("Scene 2", "The toilet is black.", "Scene 3", "The person's look", "music"):
        assert words in said["text"]
    # Polling gets the same, so a notice that lands while the chat is open shows in full.
    polled = api.get(f"/api/sessions/{chat_id(job)}/messages/?after=0").json()
    assert polled[0]["plan"] == said["plan"]
    # Kept with the job's warnings, as every code-posted notice is.
    job.refresh_from_db()
    assert job.warnings[-1]["text"] == shown.text


def test_the_yes_button_sends_a_marked_yes_naming_the_plan_and_typed_text_stays_plain(
    api: APIClient, send: Callable[..., Any]
) -> None:
    job = an_ad()
    plan = a_plan(job)
    fix_plans.show(plan)

    typed = send(job, {"text": "yes"})
    tapped = send(job, {"text": "Yes", "plan": plan.pk})

    assert typed.status_code == 201, typed.json()
    assert tapped.status_code == 201, tapped.json()
    assert typed.json()["message"]["plan"] is None
    assert tapped.json()["message"]["plan"] == {"id": plan.pk, "event": "yes", "notice": None}
    # Code tells them apart from the rows alone (what 08 routes on).
    by_text = {m.seq: m for m in Message.objects.filter(role=Message.Role.USER)}
    assert [(m.text, m.plan_event, m.fix_plan_id) for m in by_text.values()] == [
        ("yes", "", None),
        ("Yes", Message.PlanEvent.YES, plan.pk),
    ]
    # The tap only says yes: routing it is not the chat API's job, so the plan still waits.
    plan.refresh_from_db()
    assert plan.state == FixPlan.State.PROPOSED
    assert [said["plan"] for said in conversation(api, job)][1:] == [
        None,
        {"id": plan.pk, "event": "yes", "notice": None},
    ]


def test_a_yes_to_a_plan_that_no_longer_waits_is_refused_and_not_kept(
    send: Callable[..., Any],
) -> None:
    job = an_ad()
    old = a_plan(job)
    fix_plans.show(old)
    newer = a_plan(job)  # replaces the first
    fix_plans.show(newer)

    refused = send(job, {"text": "Yes", "plan": old.pk})

    assert refused.status_code == 400
    assert "newer plan" in " ".join(str(v) for v in refused.json().values())
    assert not Message.objects.filter(role=Message.Role.USER).exists()


def test_a_yes_to_another_chats_plan_is_refused(send: Callable[..., Any]) -> None:
    job, elsewhere = an_ad(), an_ad()
    theirs = a_plan(elsewhere)

    refused = send(job, {"text": "Yes", "plan": theirs.pk})

    assert refused.status_code == 400
    assert not Message.objects.filter(role=Message.Role.USER).exists()


def test_settling_a_plan_sets_its_state_and_tells_the_chat(
    api: APIClient, send: Callable[..., Any]
) -> None:
    job = an_ad()
    plan = a_plan(job)
    fix_plans.show(plan)
    send(job, {"text": "go for it"})
    yes = Message.objects.get(role=Message.Role.USER)

    settled = fix_plans.settle(plan, FixPlan.State.CONFIRMED, by=yes)

    plan.refresh_from_db()
    assert plan.state == FixPlan.State.CONFIRMED
    assert plan.confirmed_by == yes
    assert settled is not None
    last = conversation(api, job)[-1]
    assert last["role"] == "notice"
    assert last["plan"] == {"id": plan.pk, "event": "confirmed", "notice": None}
    # Reopened later, the first notice reads the plan as it stands now.
    assert conversation(api, job)[0]["plan"]["notice"]["state"] == "confirmed"

    other = a_plan(job)
    fix_plans.show(other)
    fix_plans.settle(other, FixPlan.State.DECLINED)
    other.refresh_from_db()
    assert other.state == FixPlan.State.DECLINED
    assert other.confirmed_by is None
    assert conversation(api, job)[-1]["plan"] == {
        "id": other.pk,
        "event": "declined",
        "notice": None,
    }


def test_a_plan_can_only_be_settled_as_confirmed_or_declined() -> None:
    job = an_ad()
    plan = a_plan(job)
    with pytest.raises(AssertionError):
        fix_plans.settle(plan, FixPlan.State.REPLACED)


def test_messages_from_before_plans_read_back_with_no_plan(
    api: APIClient, send: Callable[..., Any]
) -> None:
    job = an_ad()
    send(job, {"text": "Make an ad"})
    assert job.session is not None
    messages.add(job.session, role=Message.Role.AGENT, text="Here it is.")

    assert [said["plan"] for said in conversation(api, job)] == [None, None]
