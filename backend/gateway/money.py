"""Telling a paid service's "your account is out of money" from its other refusals."""

import re

import httpx

from .types import RefusedForMoney

# How the services we pay say the account has no money left: fal ("User is locked. Reason:
# Exhausted balance", 2026-11-17), Creatify ("Insufficient credits"), OpenAI's
# insufficient_quota, ElevenLabs' quota_exceeded code, and Gemini's "prepayment credits are
# depleted". Gemini's "Quota exceeded." and "exceeded your current quota" are also its plain
# rate limit, so they aren't.
_SAYS_OUT_OF_MONEY = re.compile(
    r"exhausted[ _]balance|insufficient[ _](quota|credits?|balance|funds)|not enough credits"
    r"|out of credits|credits are depleted|quota_exceeded|billing_hard_limit",
    re.IGNORECASE,
)


def refuse_if_out_of_money(response: httpx.Response, service: str) -> None:
    """Raise RefusedForMoney when `service` turned the request down because our account
    there is out of money: Payment Required, or a refusal that says so. Never retried."""
    if not 400 <= response.status_code < 500:
        return
    if response.status_code == 402 or _SAYS_OUT_OF_MONEY.search(response.text):
        raise RefusedForMoney(
            f"{service} refused it for money ({response.status_code}): {response.text[:300]}",
            service=service,
        )
