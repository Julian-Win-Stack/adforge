"""H3's voice words say the plan's gender once (pure logic)."""

import pytest

from jobs import work
from jobs.models import Job


@pytest.mark.parametrize(
    ("gender", "voice", "words"),
    [
        pytest.param(
            "woman",
            "A warm, relaxed woman in her thirties with a soft British accent.",
            "A warm, relaxed woman in her thirties with a soft British accent.",
            id="starts by naming the speaker",
        ),
        pytest.param(
            "woman",
            "An adult woman's voice with a warm tone.",
            "An adult woman's voice with a warm tone.",
            id="names a woman's voice",
        ),
        pytest.param("man", "A calm male voice.", "A calm male voice.", id="names a male voice"),
        pytest.param(
            "man",
            "Clear and upbeat, with a masculine voice.",
            "Clear and upbeat, with a masculine voice.",
            id="names a masculine voice later on",
        ),
        pytest.param(
            "man",
            "Confident, like a men's grooming coach.",
            "a man's voice. Confident, like a men's grooming coach.",
            id="a gender word about something else",
        ),
        pytest.param(
            "man",
            "Calm, not too masculine.",
            "a man's voice. Calm, not too masculine.",
            id="a gender word that is no gendered voice",
        ),
        pytest.param(
            "woman",
            "British accent, warm, BBC-style pace.",
            "a woman's voice. British accent, warm, BBC-style pace.",
            id="no gender at all, capitals kept",
        ),
        pytest.param("", "Warm and steady.", "Warm and steady.", id="planned before the gender"),
    ],
)
def test_voice_words_say_the_plans_gender_once(gender: str, voice: str, words: str) -> None:
    assert work.voice_words(Job(person_gender=gender, person_voice=voice)) == words
