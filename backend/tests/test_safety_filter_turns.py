"""Requirement 22 for agent turns: when the safety filter refuses an agent's turn over a
picture in its conversation, the turn is taken again without that picture, the agent is told
in one line which picture was left out and why, and it carries on. The chat says so in red,
with a warning line. The producer's own loop and the fixing agent, each with
its model faked at the gateway."""

from collections import deque
from collections.abc import Callable

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agents import loop
from agents.models import ToolCall
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, ToolUse, Turn, TurnRequest
from jobs.models import AgentFix, Job, ProducedItem, SceneStep

from .conftest import HeldSteps
from .test_agent_pictures import LOOKER, session  # noqa: F401 (a fixture)
from .test_fixing_agent import film_again, fixed
from .test_producer_auto_fix import FLOATS, clip_of_scene_2, failing
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)


def refused_over(label: str, *turns: Turn) -> Callable[[TurnRequest], Turn]:
    """An agent model whose safety filter refuses any turn showing the picture labelled
    `label`, and otherwise takes `turns` in order."""
    left = deque(turns)

    def taking(request: TurnRequest) -> Turn:
        if label in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return left.popleft()

    return taking


def turns_for(fake_model: FakeModel, purpose: str) -> list[TurnRequest]:
    return [each for each in fake_model.turns_taken if each.purpose == purpose]


def labels(request: TurnRequest) -> list[str]:
    return [image.label for image in request.images]


def result_of(request: TurnRequest, tool: str) -> str:
    [used] = [
        each
        for each in request.handoff.conversation
        if isinstance(each, ToolUse) and each.tool == tool
    ]
    return used.result


def results_of(request: TurnRequest, tool: str) -> list[str]:
    return [
        each.result
        for each in request.handoff.conversation
        if isinstance(each, ToolUse) and each.tool == tool
    ]


def red_notices(chat: Session | None) -> list[str]:
    assert chat is not None
    return list(
        chat.messages.filter(role=Message.Role.NOTICE, level=Message.Level.PROBLEM)
        .order_by("seq")
        .values_list("text", flat=True)
    )


def warned(job: Job) -> list[str]:
    job.refresh_from_db()
    return [each["text"] for each in job.warnings]


# --- The producer's loop --------------------------------------------------------------------

STARTING = "Scene 2, starting picture, v2 black toilet"
LEFT_OUT_STARTING = (
    f"The AI model's safety filter refused to look at {STARTING}, so the work on your ad "
    "carried on without it."
)


@pytest.mark.parametrize("has_a_job", [True, False], ids=["with its ad", "before its ad"])
def test_a_producer_turn_refused_over_a_picture_is_taken_again_without_it(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
    has_a_job: bool,
) -> None:
    job = (
        Job.objects.create(session=session, product_url="https://shop.example/products/gel")
        if has_a_job
        else None
    )
    fake_model.answer_unscripted(
        "produce",
        refused_over(
            STARTING,
            turn(calls=[("look_at_scene", {"scene": 2})]),
            turn(calls=[("jot_down", {"text": "seen"})]),
            turn("Done."),
        ),
    )

    loop.run(LOOKER, session)

    first, *refused, again, last = turns_for(fake_model, "produce")
    assert refused and all(STARTING in labels(each) for each in refused)
    # Taken again without it, and told in one line which picture was left out and why.
    assert STARTING not in labels(again)
    assert labels(again) == ["Scene 2, starting picture, v1 first version"]
    told = result_of(again, "look_at_scene")
    assert told.splitlines()[-1] == (
        f"{STARTING} isn't shown: the AI model's safety filter refused to look at it. "
        "Carry on without it."
    )
    # Every later turn leaves it out too, without asking the filter again.
    assert labels(last) == labels(again)
    assert result_of(last, "look_at_scene") == told
    # The run went on: the agent's next tool ran and it replied.
    assert ToolCall.objects.filter(tool="jot_down").exists()
    assert list(
        session.messages.filter(role=Message.Role.AGENT).values_list("text", flat=True)
    ) == ["Done."]
    assert red_notices(session) == [LEFT_OUT_STARTING]
    if job is not None:
        assert warned(job) == [LEFT_OUT_STARTING]

    # A worker started again after the reply finds it has nothing left to answer.
    loop.run(LOOKER, session)
    assert len(turns_for(fake_model, "produce")) == 3 + len(refused)


def test_a_producer_turn_refused_with_no_new_picture_still_stops(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
) -> None:
    fake_model.respond("produce", BlockedBySafetyFilter("content_policy_violation"))

    with pytest.raises(BlockedBySafetyFilter):
        loop.run(LOOKER, session)

    assert len(turns_for(fake_model, "produce")) == 1


BOTH = [STARTING, "Scene 2, starting picture, v1 first version"]


def test_a_turn_refused_over_pictures_looked_at_before_is_taken_once_more_without_them(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2}), ("look_at_scene", {"scene": 2})]),
        turn(calls=[("jot_down", {"text": "seen"})]),
        BlockedBySafetyFilter("content_policy_violation"),
        turn("Done."),
    )

    loop.run(LOOKER, session)

    # The model looked at both pictures on the turn before: one more turn, without both,
    # each named once though it was shown twice.
    *_, refused, again = turns_for(fake_model, "produce")
    assert labels(refused) == BOTH * 2
    assert labels(again) == []
    assert [each.splitlines()[-1] for each in results_of(again, "look_at_scene")] == 2 * [
        f"{BOTH[0]} and {BOTH[1]} aren't shown: the AI model's safety filter refused to look "
        "at them. Carry on without them."
    ]
    assert red_notices(session) == [
        f"The AI model's safety filter refused to look at {BOTH[0]} and {BOTH[1]}, so the "
        "work on your ad carried on without them."
    ]


def test_a_turn_refused_without_the_pictures_looked_at_before_too_stops(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2})]),
        turn(calls=[("jot_down", {"text": "seen"})]),
        BlockedBySafetyFilter("content_policy_violation"),
        BlockedBySafetyFilter("content_policy_violation"),
    )

    with pytest.raises(BlockedBySafetyFilter):
        loop.run(LOOKER, session)

    assert [labels(each) for each in turns_for(fake_model, "produce")[2:]] == [BOTH, []]
    assert red_notices(session) == []


def test_a_picture_shown_twice_is_left_out_once(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
) -> None:
    def refusing_either(request: TurnRequest) -> Turn:
        if set(BOTH) & set(labels(request)):
            raise BlockedBySafetyFilter("content_policy_violation")
        return turn("Done.")

    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2}), ("look_at_scene", {"scene": 2})]),
    )
    fake_model.answer_unscripted("produce", refusing_either)

    loop.run(LOOKER, session)

    # Without each picture, then without both: each try leaves out every showing of it.
    _, *refused, again = turns_for(fake_model, "produce")
    assert [labels(each) for each in refused] == [BOTH * 2, [STARTING] * 2, [BOTH[1]] * 2]
    assert labels(again) == []
    assert red_notices(session) == [
        f"The AI model's safety filter refused to look at {BOTH[0]} and {BOTH[1]}, so the "
        "work on your ad carried on without them."
    ]


def test_a_turn_the_filter_lets_through_never_looks_up_which_pictures_it_saw(
    fake_model: FakeModel,
    session: Session,  # noqa: F811
) -> None:
    fake_model.respond("produce", turn(calls=[("look_at_scene", {"scene": 2})]), turn("Done."))

    with CaptureQueriesContext(connection) as queries:
        loop.run(LOOKER, session)

    looked_up = 'SELECT "gateway_modelcall"."images" AS "images" FROM'
    assert not [each for each in queries.captured_queries if each["sql"].startswith(looked_up)]


# --- The fixing agent -----------------------------------------------------------------------


@pytest.fixture
def failed_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Scene 2's clip, made and judged: the step the agent is handed."""
    return clip_of_scene_2(fake_model, steps, say)


def a_photo_not_sent_with_the_clip() -> int:
    """A shop photo the agent sees first by looking at it: one its clip wasn't sent, as the
    agent is shown its clip's sent pictures from the start (spec #210 point 19)."""
    clip = ProducedItem.objects.filter(kind="clip", scene__number=2).latest("id")
    assert clip.sent is not None
    sent = {picture["file"] for picture in clip.sent["pictures"]}
    return next(
        photo.position for photo in clip.job.photos.order_by("position") if photo.file not in sent
    )


def test_a_fixing_agent_turn_refused_over_a_look_is_taken_again_and_costs_no_try(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    photo = a_photo_not_sent_with_the_clip()
    shop_photo = f"Shop photo {photo}"
    fake_model.answer_unscripted(
        "fix_scene",
        refused_over(
            shop_photo,
            turn(calls=[("look_at_shop_photo", {"photo": photo})]),
            turn(calls=[film_again()]),
            turn(calls=[("give_up", {"reason": "It can't be filmed."})]),
        ),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))

    fixed(failed_clip)

    _, refused, again, _ = turns_for(fake_model, "fix_scene")
    assert shop_photo in labels(refused)
    assert shop_photo not in labels(again)
    assert result_of(again, "look_at_shop_photo").splitlines()[-1] == (
        f"{shop_photo} isn't shown: the AI model's safety filter refused to look at it. "
        "Carry on without it."
    )
    # The refused turn filmed nothing: the one try is the one the agent asked for after.
    assert [each.number for each in AgentFix.objects.get().tries.all()] == [1]
    left_out = (
        f"The AI model's safety filter refused to look at {shop_photo}, so the fixing of "
        "scene 2 carried on without it."
    )
    job = failed_clip.scene.job
    assert left_out in red_notices(job.session)
    assert left_out in warned(job)
