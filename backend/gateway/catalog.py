"""Which model does each job, and what each model costs. Change a model here, nowhere else."""

from decimal import Decimal

MODEL_FOR_PURPOSE: dict[str, str] = {
    "check_page": "gpt-5-mini",
}

# US dollars per million tokens: (input, output). From OpenAI's pricing page.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = PRICE_PER_MILLION_TOKENS[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
