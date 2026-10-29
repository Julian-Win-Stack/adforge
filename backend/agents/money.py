from decimal import Decimal

from django.db.models import Q, Sum

from gateway.models import ModelCall


def dollars(amount: Decimal) -> str:
    """An amount in dollars, to the cent, or to its last digit when that is smaller."""
    exact = f"{amount.normalize():f}"
    cents = f"{amount:.2f}"
    return f"${exact if len(exact) > len(cents) else cents}"


def cost_of(calls: Q) -> Decimal:
    """What the model calls `calls` picks cost, each counted once, however many of the rows
    it is picked through: a fixing agent's try is one tool call starting several steps."""
    picked = ModelCall.objects.filter(calls).values("pk")
    cost: Decimal | None = ModelCall.objects.filter(pk__in=picked).aggregate(cost=Sum("cost_usd"))[
        "cost"
    ]
    return cost or Decimal(0)
