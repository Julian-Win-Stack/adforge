from decimal import Decimal


def dollars(amount: Decimal) -> str:
    """An amount in dollars, to the cent, or to its last digit when that is smaller."""
    exact = f"{amount.normalize():f}"
    cents = f"{amount:.2f}"
    return f"${exact if len(exact) > len(cents) else cents}"
