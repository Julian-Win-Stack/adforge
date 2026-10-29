"""The producer's instructions about the shop owner's notes once a scene has a clip (spec
#247): every note is the producer's, which looks and fixes it with its own tools (fix_scene,
the plan tools), so nothing says another AI takes the notes or that it has no tool for a fix.
Instructions can't be tested like code, so these check what can be: the key sentences are in
what the producer's model is given, pinned word for word so a later edit shows up as a diff."""

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

# The decider-era paragraph (#262 deleted it): another AI took the notes, and the producer
# had no tool for a fix.
GONE = [
    "the app takes the shop owner's notes on the ad: another AI reads each one",
    "you have no tool for it",
    "never restate a note, ask them to confirm it or say yes, or say you'll change something",
    "the plan shown, with each scene's change and cost",
]
# The producer sees through its look (spec #247 point 2).
CANT = (
    "You can't see pictures or videos yourself. When you need to know what a scene's clip, an "
    "earlier version, a library picture or a try of a fix shows, call look with one question: "
    "it is shown them, with what the clip was sent, and tells you in words what it sees, what "
    "is wrong, its root cause and a direction. It decides nothing: you do."
)
# A photo of something else than the product, sent before the plan (Julian 17:55).
NOT_A_PRODUCT_PHOTO = (
    "Before the ad is planned, every photo they send is a product photo: if they send one of "
    "something else, don't add it, and tell them to send it with a note on a scene once the "
    "first version is made."
)
SLOWER = "Slower can't be done: every clip plays at real-time speed."

PINNED = [
    CANT,
    NOT_A_PRODUCT_PHOTO,
    SLOWER,
]


@pytest.mark.parametrize("sentence", PINNED)
def test_a_notes_sentence_is_in_the_producers_instructions_word_for_word(sentence: str) -> None:
    assert sentence in " ".join(INSTRUCTIONS.split())


@pytest.mark.parametrize("words", GONE)
def test_nothing_says_another_ai_takes_the_notes(words: str) -> None:
    assert words not in " ".join(INSTRUCTIONS.split())


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

    # A note once the ad has a clip: the producer's, as every message is.
    owner_says(fake_model, say, NOTE_2)

    assert given
    for instructions in given:
        assert all(sentence in " ".join(instructions.split()) for sentence in PINNED)


def test_the_fix_limits_are_written_down_and_dated() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    limitations = (docs / "limitations.md").read_text()
    for limit in [
        "**Nothing says a fix worked.**",
        "**No escalation on a repeated note.**",
    ]:
        entry = next(line for line in limitations.splitlines() if limit in line)
        assert "2026-10-11" in entry
    adr = (docs / "adr" / "0005-one-agent-first.md").read_text()
    assert "_Note, 2026-10-12 (route B step 1, #137): nothing stops a running step yet." in adr
