"""A selected photo and the line that accompanies it across drawing and checking."""

from pydantic import BaseModel, field_validator


class SelectedPhoto(BaseModel):
    file: str
    line: str
    # The shared person/room picture is a produced picture, not a library item.
    library_number: int | None = None
    original_file: str | None = None

    @field_validator("library_number", mode="before")
    @classmethod
    def _old_shared_number(cls, value: object) -> object:
        return None if value == "" else value
