"""The shop owner's side of the chat: sends their messages through the app's own chat API,
runs every scene step the producer starts as a worker would, and keeps what happened.

Each message is sent the way the browser sends it, and the producer works on it before the
request returns. Scene steps it starts are held, then run oldest first, each waking the
producer when it finishes, as on the real server, until none is left."""

from dataclasses import dataclass, field
from typing import Any

from django.db.models import Sum
from rest_framework.test import APIClient

from agents import tasks
from agents.models import ToolCall
from chat.models import Message, Session
from gateway.models import ModelCall
from jobs.models import Fix, Job, ProducedItem, SceneStep

# The shop owner's answer when the producer waits on them and they have nothing else to say.
GO_AHEAD = "Yes, go ahead."


class Chat:
    def __init__(self, session_id: str | None = None) -> None:
        self.api = APIClient()
        self.held: list[int] = []
        # A step the producer starts is held, not run at once (see run_held).
        tasks.run_scene_step.delay = self.held.append  # type: ignore[method-assign,assignment]
        if session_id is None:
            made = self.api.post("/api/sessions/", {}, format="json")
            assert made.status_code == 201, made.content
            session_id = str(made.json()["id"])
        self.session_id = session_id

    @property
    def session(self) -> Session:
        return Session.objects.get(pk=self.session_id)

    def say(self, text: str) -> None:
        """The shop owner sends `text`, then every step it led to runs."""
        sent = self.api.post(
            f"/api/sessions/{self.session_id}/messages/", {"text": text}, format="json"
        )
        assert sent.status_code == 201, sent.content
        self.run_held()

    def run_held(self) -> None:
        while self.held:
            tasks.run_scene_step(self.held.pop(0))

    def last_seq(self) -> int:
        return self.session.messages.order_by("seq").values_list("seq", flat=True).last() or 0

    def said_since(self, seq: int) -> list[Message]:
        return list(self.session.messages.filter(seq__gt=seq).order_by("seq"))

    def finished_ads(self) -> int:
        job = Job.objects.filter(session_id=self.session_id).last()
        return job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).count() if job else 0


def make_first_version(chat: Chat, link: str, answers: list[str], most_rounds: int = 20) -> None:
    """The shop owner sends the product's link and answers the producer, as round 4's owner
    did, until the ad is shown: each question is answered with the next of `answers`, and
    anything else with GO_AHEAD."""
    left = list(answers)
    text = link
    for _ in range(most_rounds):
        seq = chat.last_seq()
        chat.say(text)
        if chat.finished_ads():
            return
        said = " ".join(m.text for m in chat.said_since(seq) if m.role == Message.Role.AGENT)
        text = left.pop(0) if "?" in said and left else GO_AHEAD
    raise RuntimeError(f"no ad was shown after {most_rounds} messages")


@dataclass
class Since:
    """Where the chat stood before the notes, so only what they led to is recorded."""

    seq: int
    tool_call: int
    model_call: int
    step: int
    fix: int
    ads: int


def since(chat: Chat) -> Since:
    def last(model: Any) -> int:
        found = model.objects.order_by("pk").values_list("pk", flat=True).last()
        return int(found or 0)

    return Since(
        seq=chat.last_seq(),
        tool_call=last(ToolCall),
        model_call=last(ModelCall),
        step=last(SceneStep),
        fix=last(Fix),
        ads=chat.finished_ads(),
    )


@dataclass
class Notes:
    """A case: what the shop owner sends, in order, once the ad is shown. After the last of
    them, each restatement the producer waits on is answered with GO_AHEAD, at most
    `yes_answers` times."""

    messages: list[str]
    yes_answers: int = 1


@dataclass
class Turned:
    sent: list[str] = field(default_factory=list)
    error: str = ""


def send_notes(chat: Chat, notes: Notes, most_rounds: int = 8) -> Turned:
    """The shop owner sends the case's messages, then says yes to what the producer restates,
    until the producer waits on nothing."""
    turned = Turned()
    left = list(notes.messages)
    yeses = notes.yes_answers
    for _ in range(most_rounds):
        if left:
            text = left.pop(0)
        elif yeses and _waits_on_a_yes(chat):
            yeses -= 1
            text = GO_AHEAD
        else:
            break
        turned.sent.append(text)
        try:
            chat.say(text)
        except Exception as error:  # Recorded: a crash is a finding too.
            turned.error = f"{type(error).__name__}: {error}"
            break
    return turned


def _waits_on_a_yes(chat: Chat) -> bool:
    """Whether a fix was restated since the shop owner last spoke, and not made yet."""
    spoke = chat.session.messages.filter(role=Message.Role.USER).order_by("seq").last()
    assert spoke is not None
    return Fix.objects.filter(
        job__session_id=chat.session_id,
        asked__created_at__gt=spoke.created_at,
        confirmed_by__isnull=True,
    ).exists()


def spent(start: Since) -> dict[str, str]:
    """What the notes cost, by what each call was for, as the app recorded it. Video and voices
    are faked, but recorded at what they would have cost."""
    rows = (
        ModelCall.objects.filter(pk__gt=start.model_call)
        .values("purpose")
        .annotate(usd=Sum("cost_usd"))
        .order_by("purpose")
    )
    return {row["purpose"]: f"{row['usd'] or 0:.4f}" for row in rows}
