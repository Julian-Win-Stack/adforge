"""One in-process seam for the product page's outside inputs.

Nothing installs a source in the app. An eval can scope one to its child process;
without it every call takes the live path it used before.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .page import Download, Fetched


class SavedPageMissing(RuntimeError):
    """A frozen input is missing: fail the run, never fall back to the live shop."""


class PageSource(Protocol):
    def check_where_it_points(self, url: str) -> None: ...
    def download(self, url: str, *, max_bytes: int, what: str) -> Download: ...
    def download_to(
        self, url: str, file: Path, *, max_bytes: int, most_seconds: float, what: str
    ) -> Fetched: ...
    def read_page(self, link: str, *, fresh: bool = False) -> dict[str, Any]: ...
    def marked_screenshot(self, link: str) -> tuple[dict[str, Any], bytes]: ...
    def product_record(self, link: str) -> dict[str, Any]: ...
    def player_file(self, src: str, page_url: str, folder: Path) -> Path | None: ...


_source: ContextVar[PageSource | None] = ContextVar("product_page_source", default=None)


def current_source() -> PageSource | None:
    return _source.get()


@contextmanager
def use_page_source(source: PageSource) -> Iterator[None]:
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)
