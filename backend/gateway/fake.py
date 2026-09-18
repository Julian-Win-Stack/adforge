"""A scripted stand-in for a model provider, for tests. Each call pops the next scripted
outcome for its purpose: output data to return, or an error to raise."""

from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel

from .types import ModelReply, ModelRequest


class FakeModel:
    name = "fake"
    INPUT_TOKENS = 1_000
    OUTPUT_TOKENS = 100

    def __init__(self) -> None:
        self._scripts: defaultdict[str, deque[dict[str, Any] | Exception]] = defaultdict(deque)

    def respond(self, purpose: str, *outcomes: dict[str, Any] | Exception) -> None:
        self._scripts[purpose].extend(outcomes)

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        script = self._scripts[request.purpose]
        if not script:
            raise AssertionError(f"No scripted model response left for {request.purpose!r}")
        outcome = script.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return ModelReply(
            output=request.output.model_validate(outcome),
            input_tokens=self.INPUT_TOKENS,
            output_tokens=self.OUTPUT_TOKENS,
        )
