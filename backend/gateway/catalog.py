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
    "design_voice": "inworld-tts-2",
    "measure_voice": "inworld-tts-2",
}

# US dollars per million tokens: (input, output). From OpenAI's pricing page.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-5.6-sol": (Decimal("4.00"), Decimal("20.00")),
    "gpt-5.6-terra": (Decimal("2.00"), Decimal("12.00")),
}

# US dollars per million tokens for picture models: (text input, picture output). From
# OpenAI's pricing page. Only text goes in: the person is drawn from a description.
PRICE_PER_MILLION_PICTURE_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-image-2.5-sunburst": (Decimal("5.00"), Decimal("30.00")),
}

# US dollars per million characters spoken. From Inworld's pay-as-you-go pricing. Designing a
# voice is charged at the same rate for the sample it speaks.
PRICE_PER_MILLION_CHARACTERS: dict[str, Decimal] = {
    "inworld-tts-2": Decimal("25.00"),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = PRICE_PER_MILLION_TOKENS[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def picture_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = PRICE_PER_MILLION_PICTURE_TOKENS[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def speech_cost_usd(model: str, characters: int) -> Decimal:
    return characters * PRICE_PER_MILLION_CHARACTERS[model] / 1_000_000
