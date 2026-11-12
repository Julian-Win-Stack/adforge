"""Freeze page inputs once, then serve them in-process without the live shop."""

import argparse
import json
import mimetypes
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings

from adforge.retry import OutsideServiceDown
from jobs import firecrawl, page
from jobs.page_source import SavedPageMissing, current_source

from .scenarios import Scenario, select_scenarios
from .store import Store, write_json


class SavedPageSource:
    """The outside inputs of one scenario. A broken snapshot can never go live."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        try:
            self.manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SavedPageMissing(
                f"{folder}: no complete saved page; run save-pages first"
            ) from error

    def _file(self, name: str) -> Path:
        path = self.folder / name
        if not path.resolve().is_relative_to(self.folder.resolve()) or not path.is_file():
            raise SavedPageMissing(f"Saved page file is missing or outside its folder: {path}")
        return path

    def _answer(self, name: str) -> dict[str, Any]:
        try:
            answer: dict[str, Any] = json.loads(self._file(self.manifest[name]).read_text())
            return answer
        except (OSError, ValueError, KeyError) as error:
            raise SavedPageMissing(f"Saved page's {name} answer is missing or invalid") from error

    def _link(self, link: str) -> None:
        if link not in self.manifest["page_links"]:
            raise SavedPageMissing(f"{link} is not the product page in this saved scenario")

    def check_where_it_points(self, url: str) -> None:
        if url not in self.manifest["page_links"] and url not in self.manifest["downloads"]:
            raise SavedPageMissing(f"No frozen input for {url}; the live shop was not fetched")

    def _download(self, url: str) -> dict[str, Any]:
        entry: dict[str, Any] | None = self.manifest["downloads"].get(url)
        if entry is None:
            raise SavedPageMissing(f"No frozen download for {url}; the live shop was not fetched")
        if error := entry.get("error"):
            if entry.get("error_kind") == "outside_service_down":
                raise OutsideServiceDown(error)
            if entry.get("error_kind") == "too_long":
                raise page.TooLongToFetch(error)
            raise page.PageUnreadable(error)
        return entry

    def download(self, url: str, *, max_bytes: int, what: str) -> page.Download:
        entry = self._download(url)
        file = self._file(entry["file"])
        if file.stat().st_size > max_bytes:
            raise page.PageUnreadable(
                f"{url} is too big to be a {what}, at over {max_bytes:,} bytes."
            )
        return page.Download(
            final_url=entry["final_url"],
            content=file.read_bytes(),
            content_type=entry["content_type"],
            charset=entry["charset"],
            markdown=entry.get("markdown"),
        )

    def download_to(
        self, url: str, file: Path, *, max_bytes: int, most_seconds: float, what: str
    ) -> page.Fetched:
        entry = self._download(url)
        saved = self._file(entry["file"])
        if saved.stat().st_size > max_bytes:
            raise page.TooLongToFetch(
                f"{url} is too big to be a {what}, at over {max_bytes:,} bytes."
            )
        shutil.copyfile(saved, file)
        return page.Fetched(final_url=entry["final_url"], content_type=entry["content_type"])

    def read_page(self, link: str, *, fresh: bool = False) -> dict[str, Any]:
        self._link(link)
        return self._answer("page")

    def marked_screenshot(self, link: str) -> tuple[dict[str, Any], bytes]:
        self._link(link)
        return self._answer("marked"), self._file(self.manifest["screenshot"]).read_bytes()

    def product_record(self, link: str) -> dict[str, Any]:
        self._link(link)
        return self._answer("product")

    def player_file(self, src: str, page_url: str, folder: Path) -> Path | None:
        self._link(page_url)
        entry = self.manifest.get("players", {}).get(src)
        if entry is None:
            raise SavedPageMissing(f"No frozen player video for {src}")
        if not entry.get("file"):
            raise page.PageUnreadable(entry.get("error") or f"No file was available for {src}")
        source = self._file(entry["file"])
        destination = folder / f"video{source.suffix}"
        shutil.copyfile(source, destination)
        return destination


def save_page(store: Store, scenario: Scenario, *, refresh: bool = False) -> bool:
    """Save all outside page inputs without calling a model; return False if already saved."""
    folder = store.page_dir(scenario.key)
    if (folder / "manifest.json").is_file() and not refresh:
        return False
    if not scenario.link:
        raise page.PageUnreadable(
            f"{scenario.key}: product link was not in the saved grading records"
        )
    if current_source() is not None:
        raise ValueError("Save pages needs the live source; leave the frozen replay scope first")
    if not settings.FIRECRAWL_API_KEY:
        raise ValueError("FIRECRAWL_API_KEY is required to save pages; no page was read")
    # Imported after Django is set up by the CLI (or the test/app caller).
    from jobs import photos, product_videos

    page.check_where_it_points(scenario.link)
    answer = firecrawl.read_page(scenario.link)
    marked, screenshot = firecrawl.marked_screenshot(scenario.link)
    product = firecrawl.product_record(scenario.link)
    download = firecrawl.as_download(answer, scenario.link)
    if not download.content:
        raise page.PageUnreadable(f"{scenario.link}: the page read returned no HTML")
    parsed = page.parse(download)
    marks = firecrawl.marks_in(marked)
    record = firecrawl.record_in(product)
    urls = set(parsed.photo_urls)
    for picture in photos.numbered(marks):
        urls.update((photos.best_url(picture), photos.widest_url(picture)))
    for url in photos.record_photo_urls(record or {}):
        urls.update((url, photos.full_size(url)))
    videos = [video for video in marks.get("vids", []) if isinstance(video, dict)]
    for video in videos:
        if poster := str(video.get("poster") or ""):
            urls.add(poster)
        if found := product_videos.YOUTUBE_ID.search(str(video.get("src") or "")):
            urls.add(product_videos.YOUTUBE_THUMBNAIL.format(id=found[1]))

    version = f"snapshots/{uuid4().hex}"
    # First reads finish outside the scenario folder; refreshes leave its old manifest intact.
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".save-page-", dir=folder.parent) as temporary:
        staging = Path(temporary)
        for name, value in (("page", answer), ("marked", marked), ("product", product)):
            write_json(staging / f"{name}.json", value)
        (staging / "screenshot.png").write_bytes(screenshot)
        (staging / "page.html").write_bytes(download.content)
        downloads: dict[str, dict[str, Any]] = {}

        def keep(url: str, got: page.Download) -> None:
            extension = mimetypes.guess_extension(got.content_type) or ".bin"
            name = f"{sha256(got.content).hexdigest()}{extension}"
            (staging / name).write_bytes(got.content)
            downloads[url] = {
                "file": f"{version}/{name}",
                "final_url": got.final_url,
                "content_type": got.content_type,
                "charset": got.charset,
                "markdown": got.markdown,
            }

        keep(scenario.link, download)
        if download.final_url != scenario.link:
            keep(download.final_url, download)
        for url in sorted(urls):
            try:
                keep(url, page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo"))
            except (page.PageUnreadable, OutsideServiceDown) as error:
                downloads[url] = _failed_download(error)
        players: dict[str, dict[str, Any]] = {}
        for video in videos:
            if video.get("kind") == "iframe":
                src = str(video.get("src") or "")
                if not src or src in players or product_videos.YOUTUBE_ID.search(src):
                    continue
                with tempfile.TemporaryDirectory(dir=staging) as player_folder:
                    try:
                        file = product_videos.download_player(
                            src, download.final_url, Path(player_folder)
                        )
                    except Exception as error:
                        # As in the live reader: known-unavailable players stay as links.
                        players[src] = {"file": None, "error": str(error)}
                    else:
                        name = f"player-{sha256(src.encode()).hexdigest()}{file.suffix}"
                        shutil.copyfile(file, staging / name)
                        players[src] = {"file": f"{version}/{name}"}
                continue
            src = str(video.get("src") or "")
            if src in downloads:
                continue
            fetched_file = staging / f".video-{uuid4().hex}"
            try:
                fetched = page.download_to(
                    src,
                    fetched_file,
                    max_bytes=product_videos.NEVER_ENDING_BYTES,
                    most_seconds=product_videos.NEVER_ENDING_SECONDS,
                    what="product video",
                )
                with fetched_file.open("rb") as content:
                    fingerprint = sha256()
                    for chunk in iter(lambda: content.read(1024 * 1024), b""):
                        fingerprint.update(chunk)
                extension = mimetypes.guess_extension(fetched.content_type) or ".bin"
                name = f"{fingerprint.hexdigest()}{extension}"
                fetched_file.replace(staging / name)
                downloads[src] = {
                    "file": f"{version}/{name}",
                    "final_url": fetched.final_url,
                    "content_type": fetched.content_type,
                    "charset": None,
                }
            except (page.PageUnreadable, OutsideServiceDown) as error:
                downloads[src] = _failed_download(error)
            finally:
                fetched_file.unlink(missing_ok=True)
        manifest = {
            "link": scenario.link,
            "saved_at": datetime.now(UTC).isoformat(),
            "page_links": list(dict.fromkeys((scenario.link, download.final_url))),
            "page": f"{version}/page.json",
            "marked": f"{version}/marked.json",
            "product": f"{version}/product.json",
            "screenshot": f"{version}/screenshot.png",
            "html": f"{version}/page.html",
            "downloads": downloads,
            "players": players,
        }
        destination = folder / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, destination)
        write_json(folder / "manifest.json", manifest)
    return True


def _failed_download(error: Exception) -> dict[str, str]:
    return {
        "error": str(error),
        "error_kind": "outside_service_down"
        if isinstance(error, OutsideServiceDown)
        else "too_long"
        if isinstance(error, page.TooLongToFetch)
        else "unreadable",
    }


def main(argv: Sequence[str], *, store: Store) -> int:
    import os

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    django.setup()
    parser = argparse.ArgumentParser(prog="python -m evals.regression save-pages")
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--refresh", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        scenarios = select_scenarios(args.only or args.refresh or None)
        select_scenarios(args.refresh)
        if set(args.refresh) - {scenario.key for scenario in scenarios}:
            parser.error("Every --refresh scenario must also be selected by --only")
    except ValueError as error:
        parser.error(str(error))
    failed = 0
    for scenario in scenarios:
        try:
            saved = save_page(store, scenario, refresh=scenario.key in args.refresh)
        except ValueError as error:
            print(f"Cannot save pages: {error}")
            return 1
        except (page.PageUnreadable, firecrawl.FirecrawlFailed, OutsideServiceDown) as error:
            print(f"Dead link: {scenario.key}: {error}")
            failed += 1
        else:
            print(f"{scenario.key}: {'saved' if saved else 'already saved'}")
    return int(failed > 0)
