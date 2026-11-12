"""The regression run's spending limit and shared ledger of actual paid attempts."""

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gateway.models import ModelCall
    from gateway.types import Handoff

from .store import read_json, write_json


def below_limit(spend_usd: Decimal, estimate_usd: Decimal) -> bool:
    return spend_usd < estimate_usd * Decimal("1.5")


class SpendLimitReached(BaseException):
    """An intentional eval pause, never an app tool or outside-service failure."""


class SpendLedger:
    """One file-locked account of actual calls, shared by every scenario process."""

    def __init__(self, path: Path, estimate_usd: Decimal, *, pacing: bool = True) -> None:
        self.path = path
        self.estimate_usd = estimate_usd
        self.pacing = pacing

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value: dict[str, Any] = read_json(self.path)
            value.setdefault("calls", {})
            value.setdefault("estimate_usd", str(self.estimate_usd))
            try:
                yield value
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _spent(value: dict[str, Any], scenario: str | None = None) -> Decimal:
        return sum(
            (
                Decimal(call["cost_usd"])
                for call in value["calls"].values()
                if scenario is None or call["scenario"] == scenario
            ),
            Decimal(0),
        )

    def record(
        self,
        scenario: str,
        execution: str,
        call_id: int,
        cost_usd: Decimal,
        *,
        purpose: str = "",
        video_seconds: float = 0,
    ) -> None:
        # A new child database starts its sequences again, so pk alone is not an identity.
        key = f"{execution}:{call_id}"
        with self._locked() as value:
            if key in value["calls"]:
                return
            value["calls"][key] = {
                "scenario": scenario,
                "execution": execution,
                "call_id": call_id,
                "cost_usd": str(cost_usd),
                "purpose": purpose,
                "video_seconds": video_seconds,
            }
            write_json(self.path, value)

    @property
    def spend_usd(self) -> Decimal:
        with self._locked() as value:
            return self._spent(value)

    def scenario_cost(self, scenario: str) -> Decimal:
        with self._locked() as value:
            return self._spent(value, scenario)

    def call_ids(self, execution: str) -> list[int]:
        with self._locked() as value:
            return sorted(
                call["call_id"]
                for call in value["calls"].values()
                if call["execution"] == execution
            )

    def can_start(self) -> bool:
        with self._locked() as value:
            return below_limit(self._spent(value), Decimal(value["estimate_usd"]))

    def reserve_slot(self, key: str, *, now: float, gap: float) -> float:
        """Reserve under the same lock across children; wait outside the lock."""
        with self._locked() as value:
            slots = value.setdefault("next_slots", {})
            slot = max(now, float(slots.get(key, now)))
            slots[key] = slot + gap
            write_json(self.path, value)
            return slot

    def guard(self) -> None:
        if not self.can_start():
            raise SpendLimitReached("spend reached 1.5 times the run's estimate")

    def pace(self, model: str, provider: str) -> None:
        from gateway import catalog

        if not self.pacing:
            return
        if model in catalog.PRICE_PER_MILLION_PICTURE_TOKENS:
            gap = 30.1 if provider == "azure" else 3.1
        elif model in catalog.PRICE_PER_MILLION_TOKENS:
            gap = 0.4
        else:
            return
        slot = self.reserve_slot(f"{provider}:{model}", now=time.time(), gap=gap)
        while (remaining := slot - time.time()) > 0:
            self.guard()
            time.sleep(min(remaining, 0.1))
        self.guard()

    @contextmanager
    def fal_slot(self) -> Iterator[None]:
        """Music uses fal's two concurrent request slots across all scenario children."""
        if not self.pacing:
            yield
            return
        while True:
            self.guard()
            for number in range(2):
                lock = (self.path.parent / f"fal-{number}.lock").open("a")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    continue
                try:
                    self.guard()
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    lock.close()
                return
            time.sleep(0.1)


class PaidCalls:
    def __init__(self, ledger: SpendLedger, scenario: str, execution: str) -> None:
        self.ledger, self.scenario, self.execution = ledger, scenario, execution

    @contextmanager
    def attempt(self, purpose: str, model: str, provider: str, handoff: Handoff) -> Iterator[None]:
        # A submitted clip is already paid: let its free collection save that output.
        if purpose == "collect_broll_clip":
            yield
            return
        self.ledger.guard()
        self.ledger.pace(model, provider)
        if purpose == "make_music":
            with self.ledger.fal_slot():
                yield
        else:
            self.ledger.guard()
            yield

    def recorded(self, call: ModelCall) -> None:
        self.ledger.record(
            self.scenario,
            self.execution,
            call.pk,
            call.cost_usd or Decimal(0),
            purpose=call.purpose,
            video_seconds=float(call.video_seconds or 0),
        )
