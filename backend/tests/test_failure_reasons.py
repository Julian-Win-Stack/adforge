"""Why work failed, in plain words for the shop owner (agents.loop.why_it_failed_plainly).
Each reason is shown through the chat in the scene tests; this covers one no chat test
reaches."""

from agents.loop import why_it_failed, why_it_failed_plainly
from gateway.gateway import UnreadableImage
from gateway.types import NotSetUp


def test_a_picture_no_model_can_read_says_which_kinds_they_can() -> None:
    assert why_it_failed_plainly(UnreadableImage("photo.heic is a HEIC")) == (
        "a picture was in a kind models can't read: only PNG, JPEG, WebP or GIF."
    )


def test_a_service_with_no_key_says_which_setting_is_missing() -> None:
    error = NotSetUp("Pictures are drawn on OpenAI's own API, and OPENAI_API_KEY is not set")

    assert why_it_failed_plainly(error) == (
        "the app isn't set up for it: Pictures are drawn on OpenAI's own API, and "
        "OPENAI_API_KEY is not set."
    )
    assert why_it_failed(error, "the tool") == (
        "the app isn't set up for it (Pictures are drawn on OpenAI's own API, and "
        "OPENAI_API_KEY is not set). Trying again won't help."
    )
