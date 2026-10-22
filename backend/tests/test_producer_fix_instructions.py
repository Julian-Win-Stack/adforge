"""The producer's instructions about the shop owner's notes once a scene has a clip (route B
step 1, #137; fix redesign, decisions 1, 4, 15 and 20): the producer decides and makes no fix,
has no tool for one, and answers what isn't a note from the app's messages. Instructions
can't be tested like code, so these check what can be: the key sentences are in what the
producer's model is given, pinned word for word so a later edit shows up as a diff. How a
note becomes a plan, and the plan a fix, is tested in tests/test_fix_wiring.py and
tests/test_run_plan.py."""

from pathlib import Path

import pytest

from agents.producer import INSTRUCTIONS
from gateway.fake import FakeModel
from gateway.types import TurnReply, TurnRequest
from jobs.models import ProducedItem

from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_fixes import (
    NOTE_2,
    Say,
    owner_says,
    shown,  # noqa: F401 (a fixture)
)

# Another AI reads the notes and code shows the plan with a yes button (decisions 1, 4, 15).
THE_APP_TAKES_THE_NOTES = (
    "Once a scene has a clip, the app takes the shop owner's notes on the ad: another AI "
    "reads each one and looks at the pictures and clips, then the app shows them a plan in the "
    "chat with a yes button, and once they say yes it makes every change and shows the new ad."
)
NO_FIX_ROLE = (
    "You don't decide what changes or make it, and you have no tool for it: never restate a "
    "note, ask them to confirm it or say yes, or say you'll change something."
)
WHAT_REACHES_YOU = (
    "What reaches you from them then is what isn't a note, such as a question: answer it."
)
# The system notes the producer is told by (decision 20).
TOLD_BY_THE_APP = (
    "You are told what the app does in messages from the app, not from the shop owner: the "
    "plan shown, with each scene's change and cost; the changes started; each scene done and "
    "what it cost; a check that found a change missing; an error; the new ad shown."
)
ANSWER_FROM_THOSE = (
    "Answer their questions from those, such as what is happening with a scene or what it "
    "costs, and don't repeat what the chat already showed them."
)
NEVER_SAY_DONE = (
    "Never say a change is done before the app says so, and never say it is right: you can't "
    "see pictures or videos."
)
CANT = "What you can't do, said plainly the same way each time: see pictures or videos."
# A photo of something else than the product, sent before the plan (Julian 17:55).
NOT_A_PRODUCT_PHOTO = (
    "Before the ad is planned, every photo they send is a product photo: if they send one of "
    "something else, don't add it, and tell them to send it with a note on a scene once the "
    "first version is made."
)
SLOWER = "Slower can't be done: every clip plays at real-time speed."

PINNED = [
    THE_APP_TAKES_THE_NOTES,
    NO_FIX_ROLE,
    WHAT_REACHES_YOU,
    TOLD_BY_THE_APP,
    ANSWER_FROM_THOSE,
    NEVER_SAY_DONE,
    CANT,
    NOT_A_PRODUCT_PHOTO,
    SLOWER,
]


@pytest.mark.parametrize("sentence", PINNED)
def test_a_notes_sentence_is_in_the_producers_instructions_word_for_word(sentence: str) -> None:
    assert sentence in " ".join(INSTRUCTIONS.split())


# Only this test runs the chat, so only it has the database.
@pytest.mark.django_db(transaction=True)
def test_the_producers_model_is_given_the_notes_instructions(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[str] = []
    take_turn = fake_model.take_turn

    def keeping(request: TurnRequest) -> TurnReply:
        if request.purpose == "produce":
            given.append(request.instructions)
        return take_turn(request)

    monkeypatch.setattr(fake_model, "take_turn", keeping)

    # Not a note, as the decider sorts it unscripted: passed on to the producer.
    owner_says(fake_model, say, NOTE_2)

    assert given
    for instructions in given:
        assert all(sentence in " ".join(instructions.split()) for sentence in PINNED)


def test_the_fix_limits_are_written_down_and_dated() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    limitations = (docs / "limitations.md").read_text()
    for limit in [
        "**No stop button for a fix.**",
        "**Nothing says a fix worked.**",
        "**No escalation on a repeated note.**",
    ]:
        entry = next(line for line in limitations.splitlines() if limit in line)
        assert "2026-10-11" in entry
    adr = (docs / "adr" / "0005-one-agent-first.md").read_text()
    assert "_Note, 2026-10-12 (route B step 1, #137): nothing stops a running step yet." in adr
