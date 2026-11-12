"""Presenter words for authored prompt templates, before adding owner or model data.

The existing templates are written in the default feminine voice. Keep that version
unchanged for older jobs; render only those templates, never supplied dialogue, page
facts or the boxes a model filled in.
"""

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, cast

from pydantic import BaseModel

from gateway.types import ToolSpec


@dataclass(frozen=True)
class PresenterWords:
    subject: str
    object: str
    possessive: str
    person: str
    reflexive: str

    def render(self, template: str) -> str:
        """Fill an authored feminine template with this presenter's words."""
        if self.subject == "she":
            return template
        # Object 'her' differs from possessive 'her'. These grammatical contexts
        # occur in our authored templates (call her, tell her, close to her, ...).
        template = re.sub(
            r"\b(call|tell) (her)\b|\b(her)(?=[,.;!?]| (?:at|with|using|sways|where|that)\b)",
            lambda match: (
                f"{match[1]} {self.object}"
                if match[1]
                else self.object.capitalize()
                if match[0][0].isupper()
                else self.object
            ),
            template,
            flags=re.IGNORECASE,
        )
        replacements = {
            "she": self.subject,
            "her": self.possessive,
            "herself": self.reflexive,
            "the woman": self.person,
        }
        return re.sub(
            r"\b(she|her|herself|the woman)\b",
            lambda match: (
                replacements[match[0].lower()].capitalize()
                if match[0][0].isupper()
                else replacements[match[0]]
            ),
            template,
            flags=re.IGNORECASE,
        )

    def tool(self, spec: ToolSpec) -> ToolSpec:
        """The authored tool descriptions filled for this presenter."""
        return replace(
            spec, description=self.render(spec.description), arguments=self.form(spec.arguments)
        )

    def form[Model: BaseModel](self, model: type[Model]) -> type[Model]:
        """A per-gender schema; fields and validation keep their original behavior."""
        return cast(type[Model], _form(model, self))


def presenter_words(gender: str) -> PresenterWords:
    """Missing gender preserves the words used before gender-aware prompts."""
    if gender == "man":
        return PresenterWords("he", "him", "his", "the man", "himself")
    return PresenterWords("she", "her", "her", "the woman", "herself")


@lru_cache(maxsize=128)
def _form(model: type[BaseModel], words: PresenterWords) -> type[BaseModel]:
    if words.subject == "she":
        return model

    def descriptions(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    node[key] = words.render(value)
                else:
                    descriptions(value)
        elif isinstance(node, list):
            for value in node:
                descriptions(value)

    def schema(cls: type[BaseModel], *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = model.model_json_schema(*args, **kwargs)
        descriptions(result)
        return result

    return type(model.__name__, (model,), {"model_json_schema": classmethod(schema)})
