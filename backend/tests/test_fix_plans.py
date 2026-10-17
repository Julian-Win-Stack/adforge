"""Fix plans (fix redesign, decisions 8, 13, 19): the record of what the decider plans to
change in each scene from the shop owner's notes of one round, and what the chat shows of it.
Nothing runs a plan yet; these tests pin its shape and the rules it keeps by itself."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from agents.models import ToolCall
from chat.models import Message, Session
from gateway.models import ModelCall
from jobs import fix_plans
from jobs.models import FixPlan, FixPlanScene, Job, Scene, SceneStep

pytestmark = pytest.mark.django_db

NOTES = [
    {"seq": 7, "text": "make the toilet black in scene 2"},
    {"seq": 8, "text": "and I liked the first version of scene 3 better"},
]


def an_ad(scenes: int = 5) -> Job:
    """A job in a chat, with `scenes` B-roll scenes."""
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/products/gel")
    for number in range(1, scenes + 1):
        Scene.objects.create(
            job=job, number=number, line=f"Line {number}.", shows=f"Scene {number} shows it."
        )
    return job


def chat_of(job: Job) -> Session:
    assert job.session is not None
    return job.session


def row(job: Job, number: int, action: FixPlanScene.Action, **fields: object) -> FixPlanScene:
    return FixPlanScene(scene=job.scenes.get(number=number), action=action, **fields)


def every_kind(job: Job) -> list[FixPlanScene]:
    """One row of each action, the photo answers on the new picture."""
    return [
        row(
            job,
            1,
            FixPlanScene.Action.NEW_PICTURE,
            change="The toilet is black.",
            estimate_usd=Decimal("0.55"),
            estimate_up_to_usd=Decimal("1.10"),
            photos=[
                {
                    "photo": 2,
                    "file": "owner/toilet.jpg",
                    "thing": "black toilet",
                    "in_picture": True,
                    "during_action": False,
                }
            ],
        ),
        row(
            job,
            2,
            FixPlanScene.Action.NEW_CLIP,
            change="The hand scrubs faster.",
            estimate_usd=Decimal("0.45"),
            estimate_up_to_usd=Decimal("0.90"),
        ),
        row(
            job,
            3,
            FixPlanScene.Action.NEW_LINE,
            change="Say the new line.",
            line="It cleans in one wipe.",
            estimate_usd=Decimal("0.70"),
            estimate_up_to_usd=Decimal("1.25"),
        ),
        row(job, 4, FixPlanScene.Action.GO_BACK, change="Back to the first one.", go_back_to=1),
        row(
            job,
            5,
            FixPlanScene.Action.NO_CHANGE,
            change="The music stays.",
            cant="The music can't be changed.",
        ),
    ]


def test_a_plan_with_every_kind_of_change_reads_back_as_the_chat_shows_it() -> None:
    job = an_ad()

    plan = fix_plans.propose(job, NOTES, every_kind(job), question="Black or dark grey?")

    assert fix_plans.notice(FixPlan.objects.get(pk=plan.pk)) == {
        "plan": plan.pk,
        "state": "proposed",
        "question": "Black or dark grey?",
        "estimate_usd": "1.700000",
        "estimate_up_to_usd": "3.250000",
        "scenes": [
            {
                "scene": 1,
                "action": "new_picture",
                "change": "The toilet is black.",
                "line": "",
                "go_back_to": None,
                "cant": "",
                "estimate_usd": "0.550000",
                "estimate_up_to_usd": "1.100000",
                "photos": [
                    {
                        "photo": 2,
                        "thing": "black toilet",
                        "in_picture": True,
                        "during_action": False,
                    }
                ],
            },
            {
                "scene": 2,
                "action": "new_clip",
                "change": "The hand scrubs faster.",
                "line": "",
                "go_back_to": None,
                "cant": "",
                "estimate_usd": "0.450000",
                "estimate_up_to_usd": "0.900000",
                "photos": [],
            },
            {
                "scene": 3,
                "action": "new_line",
                "change": "Say the new line.",
                "line": "It cleans in one wipe.",
                "go_back_to": None,
                "cant": "",
                "estimate_usd": "0.700000",
                "estimate_up_to_usd": "1.250000",
                "photos": [],
            },
            {
                "scene": 4,
                "action": "go_back",
                "change": "Back to the first one.",
                "line": "",
                "go_back_to": 1,
                "cant": "",
                "estimate_usd": "0.000000",
                "estimate_up_to_usd": "0.000000",
                "photos": [],
            },
            {
                "scene": 5,
                "action": "no_change",
                "change": "The music stays.",
                "line": "",
                "go_back_to": None,
                "cant": "The music can't be changed.",
                "estimate_usd": "0.000000",
                "estimate_up_to_usd": "0.000000",
                "photos": [],
            },
        ],
    }
    # What the owner wrote is kept word for word, and the photo's file for the steps.
    assert plan.notes == NOTES
    assert plan.scenes.get(scene__number=1).photos[0]["file"] == "owner/toilet.jpg"
    assert plan.session == chat_of(job)


def test_a_plan_that_changes_nothing_is_still_a_plan_the_owner_sees() -> None:
    job = an_ad(1)

    plan = fix_plans.propose(job, NOTES, [], question="Which scene do you mean?")

    shown = fix_plans.notice(plan)
    assert shown["scenes"] == []
    assert shown["estimate_usd"] == "0.000000"
    assert shown["question"] == "Which scene do you mean?"


def test_a_new_plan_replaces_the_one_the_owner_hasnt_answered() -> None:
    job = an_ad(2)
    first = fix_plans.propose(job, NOTES[:1], [row(job, 2, FixPlanScene.Action.NEW_PICTURE)])
    confirmed = fix_plans.propose(job, NOTES[:1], [])
    confirmed.state = FixPlan.State.CONFIRMED
    confirmed.save()
    second = fix_plans.propose(job, NOTES, [row(job, 2, FixPlanScene.Action.NEW_CLIP)])

    first.refresh_from_db()
    confirmed.refresh_from_db()
    assert first.state == FixPlan.State.REPLACED
    assert confirmed.state == FixPlan.State.CONFIRMED
    assert second.state == FixPlan.State.PROPOSED
    assert fix_plans.open_plan(chat_of(job)) == second
    # Plans in other chats are left alone.
    other = an_ad(1)
    elsewhere = fix_plans.propose(other, NOTES, [])
    assert fix_plans.open_plan(chat_of(job)) == second
    assert fix_plans.open_plan(chat_of(other)) == elsewhere


def test_the_database_refuses_two_open_plans_in_one_chat() -> None:
    job = an_ad(1)
    fix_plans.propose(job, NOTES, [])

    with pytest.raises(IntegrityError), transaction.atomic():
        FixPlan.objects.create(job=job, session=chat_of(job), notes=NOTES)


def test_the_yes_is_kept_on_the_plan() -> None:
    job = an_ad(1)
    plan = fix_plans.propose(job, NOTES, [])
    yes = Message.objects.create(session=chat_of(job), seq=9, role=Message.Role.USER, text="yes")

    plan.state = FixPlan.State.CONFIRMED
    plan.confirmed_by = yes
    plan.save()

    assert FixPlan.objects.get().confirmed_by == yes
    assert fix_plans.open_plan(chat_of(job)) is None


def test_a_plan_has_cost_what_its_scenes_steps_were_billed() -> None:
    job = an_ad(2)
    plan = fix_plans.propose(
        job,
        NOTES,
        [
            row(job, 1, FixPlanScene.Action.NEW_PICTURE),
            row(job, 2, FixPlanScene.Action.NEW_CLIP),
        ],
    )
    picture, clip = plan.scenes.all()
    assert plan.cost_usd() == 0

    for number, (scene, kind, costs) in enumerate(
        [
            (picture, SceneStep.Kind.STARTING_PICTURE, ["0.05", "0.01"]),
            (clip, SceneStep.Kind.CLIP, ["0.40"]),
        ]
    ):
        call = ToolCall.objects.create(
            session=chat_of(job), agent="test", tool="step", call_id=f"c{number}", arguments={}
        )
        SceneStep.objects.create(
            scene=scene.scene, kind=kind, tool_call=call, line=scene.scene.line, plan_scene=scene
        )
        for cost in costs:
            ModelCall.objects.create(
                session=chat_of(job),
                job=job,
                tool_call=call,
                purpose="test",
                provider="fake",
                model="fake",
                attempt=1,
                handoff={},
                outcome=ModelCall.Outcome.SUCCEEDED,
                cost_usd=Decimal(cost),
                duration_ms=1,
            )
    # A step made for no plan isn't counted.
    loose = ToolCall.objects.create(
        session=chat_of(job), agent="test", tool="step", call_id="loose", arguments={}
    )
    ModelCall.objects.create(
        tool_call=loose,
        purpose="test",
        provider="fake",
        model="fake",
        attempt=1,
        handoff={},
        outcome=ModelCall.Outcome.SUCCEEDED,
        cost_usd=Decimal("9"),
        duration_ms=1,
    )

    assert picture.cost_usd() == Decimal("0.06")
    assert clip.cost_usd() == Decimal("0.40")
    assert plan.cost_usd() == Decimal("0.46")
