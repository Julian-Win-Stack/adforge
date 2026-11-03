import pytest

from agents.owner_quotes import QuoteNotFound, check_quotes
from chat.models import Message


def test_quotes_keep_their_source_messages_and_the_producers_order() -> None:
    earlier = Message(role="user", text="Use the blue cup. Keep it on the desk.", seq=1)
    later = Message(role="user", text="Only scene two needs a hand.", seq=5)

    checked = check_quotes([earlier, later], ["Only scene two", "blue cup"])

    assert [(quote.text, quote.message) for quote in checked] == [
        ("Only scene two", later),
        ("blue cup", earlier),
    ]


def test_matching_ignores_spacing_quote_marks_and_capitals_only() -> None:
    owner = Message(role="user", text="Use the “BLUE”   cup,\nthen show its handle.")

    assert check_quotes([owner], ['"blue" cup, then show'])[0].message is owner


@pytest.mark.parametrize("role,pasted_page", [("agent", False), ("notice", False), ("user", True)])
def test_other_sources_cannot_supply_the_owners_words(role: str, pasted_page: bool) -> None:
    message = Message(role=role, text="Make the cup blue", pasted_page=pasted_page)

    with pytest.raises(
        QuoteNotFound, match="^that text isn't in the owner's messages, quote it exactly$"
    ):
        check_quotes([message], ["Make the cup blue"])


@pytest.mark.parametrize(
    "quote", ["cupboard", "up", "bluecup", "cup blue", "blue red cup", "", "“”"]
)
def test_a_quote_must_be_a_contiguous_chunk_of_whole_words(quote: str) -> None:
    owner = Message(role="user", text="Show the blue cup on the desk.")

    with pytest.raises(QuoteNotFound):
        check_quotes([owner], [quote])


def test_quotes_cannot_be_spliced_across_messages() -> None:
    messages = [Message(role="user", text="Make it blue"), Message(role="user", text="on the desk")]

    with pytest.raises(QuoteNotFound):
        check_quotes(messages, ["blue on the desk"])


def test_a_missing_quote_refuses_the_whole_set() -> None:
    owner = Message(role="user", text="Show the blue cup.")

    with pytest.raises(QuoteNotFound):
        check_quotes([owner], ["blue cup", "red cup"])
