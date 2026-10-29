"""The shop owner's side of a fix, for tests about the worker's part of it rather than about
the chat. Nothing here takes a producer turn."""

from chat import messages
from chat.models import Message, Session


def owner_says(text: str) -> Message:
    """The shop owner's message, as the chat keeps it, with nothing woken by it."""
    return messages.add(Session.objects.get(), role=Message.Role.USER, text=text)
