"""Which model does each job, and what each model costs. Change a model here, nowhere else."""

from decimal import Decimal

MODEL_FOR_PURPOSE: dict[str, str] = {
    "produce": "gpt-5.6-sol",
    "check_page": "gpt-5-mini",
    "plan_ad": "gpt-5.6-sol",
    "fact_check": "gpt-5.6-terra",
    "rewrite_line": "gpt-5.6-sol",
    "shorten_script": "gpt-5.6-sol",
    "draw_person": "gpt-image-2.5-sunburst",
    "choose_starting_picture": "gpt-5.6-sol",
    "make_starting_picture": "gpt-image-2.5-sunburst",
    "design_voice": "inworld-tts-2",
    "measure_voice": "inworld-tts-2",
    "speak_line": "inworld-tts-2",
    "transcribe_line": "scribe_v2",
    # A clip is asked for, then waited for and fetched: two calls, only the first paid.
    "make_clip": "heygen-avatar-iv",
    "collect_clip": "heygen-avatar-iv",
    "make_music": "sonilo/v1.1/text-to-music",
}

# US dollars per million tokens: (input, output). From OpenAI's pricing page.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-5.6-sol": (Decimal("4.00"), Decimal("20.00")),
    "gpt-5.6-terra": (Decimal("2.00"), Decimal("12.00")),
}

# US dollars per million tokens for picture models: (text input, picture input, picture
# output). From OpenAI's pricing page. Pictures go in when one is made from others, such as
# a scene's starting picture from the portrait and a product photo.
PRICE_PER_MILLION_PICTURE_TOKENS: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "gpt-image-2.5-sunburst": (Decimal("5.00"), Decimal("8.00"), Decimal("30.00")),
}

# US dollars per million characters spoken. From Inworld's pay-as-you-go pricing. Designing a
# voice is charged at the same rate for the sample it speaks.
PRICE_PER_MILLION_CHARACTERS: dict[str, Decimal] = {
    "inworld-tts-2": Decimal("25.00"),
}

# US dollars per hour of audio heard. From ElevenLabs' API pricing page (Scribe, not realtime).
PRICE_PER_HOUR_OF_AUDIO: dict[str, Decimal] = {
    "scribe_v2": Decimal("0.22"),
}

# US dollars per second of video made. HeyGen doesn't publish one: measured from its wallet,
# which a 5.72-second clip took $0.20 from (docs/video-model-tests.md).
PRICE_PER_SECOND_OF_VIDEO: dict[str, Decimal] = {
    "heygen-avatar-iv": Decimal("0.035"),
}


# US dollars per second of music made. From fal's model page on 2026-09-17
# (docs/video-model-tests.md).
PRICE_PER_SECOND_OF_MUSIC: dict[str, Decimal] = {
    "sonilo/v1.1/text-to-music": Decimal("0.0025"),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = PRICE_PER_MILLION_TOKENS[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def picture_cost_usd(
    model: str, input_tokens: int, output_tokens: int, picture_input_tokens: int = 0
) -> Decimal:
    """What a picture cost. `picture_input_tokens` are the part of `input_tokens` that were
    pictures."""
    text_price, picture_in_price, output_price = PRICE_PER_MILLION_PICTURE_TOKENS[model]
    text_tokens = input_tokens - picture_input_tokens
    return (
        text_tokens * text_price
        + picture_input_tokens * picture_in_price
        + output_tokens * output_price
    ) / 1_000_000


def speech_cost_usd(model: str, characters: int) -> Decimal:
    return characters * PRICE_PER_MILLION_CHARACTERS[model] / 1_000_000


def transcription_cost_usd(model: str, seconds: float) -> Decimal:
    return Decimal(str(seconds)) * PRICE_PER_HOUR_OF_AUDIO[model] / 3_600


def video_cost_usd(model: str, seconds: float) -> Decimal:
    return Decimal(str(seconds)) * PRICE_PER_SECOND_OF_VIDEO[model]


def music_cost_usd(model: str, seconds: int) -> Decimal:
    return seconds * PRICE_PER_SECOND_OF_MUSIC[model]
