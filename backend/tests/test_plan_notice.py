"""The plan notice and its yes button (fix redesign, flow steps 6-7, decision 4), tested the
way the browser meets them: code posts a plan in the chat, the chat API reads it back with
the plan's fields, and the yes button sends a marked message that code can tell from a
typed one. Nothing here routes the yes or runs the plan (ticket 08)."""

import threading
import time
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
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
    said = Message.objects.filter(role=Message.Role.USER)
    assert [(m.text, m.plan_event, m.fix_plan_id) for m in said] == [
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


def test_a_second_tap_on_one_plan_is_refused_and_a_tap_only_ever_says_yes(
    send: Callable[..., Any],
) -> None:
    job = an_ad()
    plan = a_plan(job)
    fix_plans.show(plan)

    # Whatever text a tap carries, it is kept as the button's yes.
    first = send(job, {"text": "no, scrap it", "plan": plan.pk})
    # The reply to the first tap was lost, so the button came back and was tapped again.
    again = send(job, {"text": "Yes", "plan": plan.pk})

    assert first.status_code == 201, first.json()
    assert again.status_code == 400
    assert again.json() == {"plan": "You've already said yes to that plan."}
    assert [(m.text, m.plan_event) for m in Message.objects.filter(role=Message.Role.USER)] == [
        ("Yes", Message.PlanEvent.YES)
    ]


def while_holding_its_locks(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, first: Callable[[], object]
) -> Callable[[Callable[[], object]], dict[str, object]]:
    """Run `first` in a thread, and `then` in another once `first` reaches `module.name`
    (where it holds its locks), slowed there so `then` runs in the middle of it. Gives what
    each gave, or the error each raised."""
    real = getattr(module, name)
    reached = threading.Event()

    def slowly(*args: Any, **kwargs: Any) -> Any:
        reached.set()
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, slowly)

    def racing(then: Callable[[], object]) -> dict[str, object]:
        outcome: dict[str, object] = {}

        def run(key: str, call: Callable[[], object], after: threading.Event | None) -> None:
            if after is not None:
                after.wait(10)
            try:
                outcome[key] = call()
            except Exception as error:
                outcome[key] = repr(error)
            finally:
                connection.close()

        running = [
            threading.Thread(target=run, args=("first", first, None)),
            threading.Thread(target=run, args=("then", then, reached)),
        ]
        for thread in running:
            thread.start()
        for thread in running:
            thread.join(30)
        return outcome

    return racing


def test_a_tap_while_the_plan_is_being_replaced_waits_for_it_instead_of_deadlocking(
    fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The yes button and a new plan both lock the chat and the plan. Taken in opposite
    orders, Postgres killed the new plan ("deadlock detected") and kept the yes to the old
    one, the plan the owner was correcting."""
    job = an_ad()
    old = a_plan(job)
    fix_plans.show(old)
    fake_model.respond("produce", turn())

    def tap() -> int:
        return (
            APIClient()
            .post(
                f"/api/sessions/{chat_id(job)}/messages/",
                {"text": "Yes", "plan": old.pk},
                format="json",
            )
            .status_code
        )

    race = while_holding_its_locks(monkeypatch, messages, "add", tap)

    assert race(lambda: a_plan(job).state) == {"first": 201, "then": FixPlan.State.PROPOSED}
    old.refresh_from_db()
    # The tap landed first; the newer plan replaced the one it said yes to.
    assert old.state == FixPlan.State.REPLACED


def test_settling_a_plan_while_a_new_one_is_proposed_waits_instead_of_deadlocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = an_ad()
    old = a_plan(job)

    race = while_holding_its_locks(
        monkeypatch,
        fix_plans,
        "post_notice",
        lambda: fix_plans.settle(old, FixPlan.State.DECLINED) is not None,
    )

    assert race(lambda: a_plan(job).state) == {"first": True, "then": FixPlan.State.PROPOSED}
    old.refresh_from_db()
    assert old.state == FixPlan.State.DECLINED


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


def test_a_plan_can_only_be_settled_as_confirmed_or_declined_and_only_once() -> None:
    job = an_ad()
    plan = a_plan(job)
    with pytest.raises(AssertionError, match="confirmed or declined"):
        fix_plans.settle(plan, FixPlan.State.REPLACED)

    fix_plans.settle(plan, FixPlan.State.DECLINED)
    with pytest.raises(AssertionError, match="only a waiting plan"):
        fix_plans.settle(plan, FixPlan.State.CONFIRMED)
    plan.refresh_from_db()
    assert plan.state == FixPlan.State.DECLINED


def test_the_plan_notice_says_the_whole_plan_in_words() -> None:
    job = an_ad(scenes=4)
    plan = fix_plans.propose(
        job,
        NOTES,
        [
            FixPlanScene(
                scene=job.scenes.get(number=1),
                action=FixPlanScene.Action.NEW_LINE,
                change="Say it shorter.",
                line="One wipe.",
                estimate_usd=Decimal("0.4"),
                estimate_up_to_usd=Decimal("0.81"),
            ),
            FixPlanScene(
                scene=job.scenes.get(number=2),
                action=FixPlanScene.Action.GO_BACK,
                change="Back to the first one.",
                go_back_to=1,
            ),
            FixPlanScene(
                scene=job.scenes.get(number=3),
                action=FixPlanScene.Action.NEW_CLIP,
                change="The hand scrubs the tile.",
                cant="Scene 3 is a talking scene.",
            ),
        ],
        question="Which tile, the white one?",
        cants=[{"kind": "music", "text": "The music can't be changed."}],
    )

    shown = fix_plans.show(plan)

    assert shown is not None
    assert shown.text == "\n".join(
        [
            "Here's the plan for your notes.",
            'Scene 1, new line: Say it shorter. New line: "One wipe." (about $0.40, up to $0.81)',
            "Scene 2, go back to version 1: Back to the first one. (free)",
            "Scene 3, new clip: The hand scrubs the tile. Can't: Scene 3 is a talking scene.",
            "Can't: The music can't be changed.",
            "Which tile, the white one?",
            "Tap Yes to go ahead, or type what to change.",
        ]
    )


def test_messages_from_before_plans_read_back_with_no_plan(
    api: APIClient, send: Callable[..., Any]
) -> None:
    job = an_ad()
    send(job, {"text": "Make an ad"})
    assert job.session is not None
    messages.add(job.session, role=Message.Role.AGENT, text="Here it is.")

    assert [said["plan"] for said in conversation(api, job)] == [None, None]
