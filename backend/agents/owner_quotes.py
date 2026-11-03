"""Check the producer's quotes against the owner's own chat, keeping their sources."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from chat.models import Message, Session


class QuoteNotFound(ValueError):
    def __init__(self) -> None:
        super().__init__("that text isn't in the owner's messages, quote it exactly")


@dataclass(frozen=True)
class CheckedQuote:
    text: str
    message: Message


def check_quotes(messages: Iterable[Message], quotes: Sequence[str]) -> list[CheckedQuote]:
    """Return every quote with its source, or refuse the whole set."""
    sources = [
        (message, _words(message.text))
        for message in messages
        if message.role == Message.Role.USER and not message.pasted_page
    ]
    checked = []
    for quote in quotes:
        words = _words(quote)
        source = next(
            (
                message
                for message, text in sources
                if words
                and any(text[start : start + len(words)] == words for start in range(len(text)))
            ),
            None,
        )
        if source is None:
            raise QuoteNotFound
        checked.append(CheckedQuote(quote, source))
    return checked


def check_session_quotes(session: Session, quotes: Sequence[str]) -> list[CheckedQuote]:
    return check_quotes(session.messages.order_by("seq"), quotes)


def _words(text: str) -> list[str]:
    # Quotation marks are decoration; apostrophes inside words are part of those words.
    normalized = text.translate(str.maketrans("‘’", "''"))
    unquoted = normalized.translate(str.maketrans("", "", '"“”«»„‟‹›'))
    unquoted = re.sub(r"(?<!\w)'|'(?!\w)", "", unquoted)
    return re.findall(r"\w+(?:'\w+)*|[^\w\s]", unquoted.casefold())
