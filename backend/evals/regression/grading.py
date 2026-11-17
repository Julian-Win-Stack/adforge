"""Build the regression page with the grading-page skill's renderer and media layout."""

import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import PIL.Image

from .store import Store, atomic_write_text, read_json, write_json

_PICTURES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
# A dropped page photo is shown this big at most: it is only there to be recognised.
DROPPED_SIDE = 512
SKILL = Path(__file__).resolve().parents[3] / ".claude/skills/grading-page/scripts"


# One picture bundle holds at most this many base64 characters: under the host's 20 MiB file
# limit. The host commits uploads one at a time at ~0.6-0.9 s each whatever their size, so a
# page's hundreds of small pictures travel in a few bundles and only videos go up one by one.
PACK_CHARS = 19_000_000
# The Artifact tool takes at most this many files in one `file_paths` upload.
UPLOAD_BATCH = 25
UPLOADS = "up"
_AUDIO = frozenset({".m4a"})


def _encode_picture(job: tuple[str, int | None, str | None]) -> bytes:
    """One picture as WebP, full size unless `max_side` is given. Reused from the cache folder
    when the same source bytes were encoded before (a baseline run's pictures never change)."""
    source, max_side, cache = job
    data = Path(source).read_bytes()
    cached = None
    if cache:
        cached = Path(cache) / f"{hashlib.sha1(data).hexdigest()}-{max_side or 0}.webp"
        if cached.is_file():
            return cached.read_bytes()
    out = io.BytesIO()
    with PIL.Image.open(io.BytesIO(data)) as image:
        if max_side:
            image.thumbnail((max_side, max_side))
        # Near-lossless: page media would otherwise pass the host's 1 GiB asset limit.
        image.save(out, "WEBP", quality=80 if max_side else 90, method=4)
    encoded = out.getvalue()
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_suffix(f".{os.getpid()}.part")
        partial.write_bytes(encoded)
        partial.replace(cached)
    return encoded


def _ffmpeg(*arguments: str | Path, timeout: int = 600) -> None:
    subprocess.run(
        [os.environ.get("FFMPEG", "ffmpeg"), "-v", "error", "-y", *map(str, arguments)],
        capture_output=True,
        check=True,
        timeout=timeout,
    )


class Media:
    """Full-resolution page media laid out for the fewest uploads: each video its own file,
    pictures packed into base64 text bundles that the page turns back into pictures."""

    def __init__(self, output: Path, cache: Path | None = None) -> None:
        self.output = output
        self.cache = cache
        # Media key -> upload path (videos) or [bundle number, start, length, type] (pictures).
        self.urls: dict[str, Any] = {}
        self.packs: list[str] = []
        self.reencoded: list[str] = []
        self._pictures: dict[str, tuple[Path, int | None]] = {}
        self._videos: dict[str, str] = {}
        self._reencoded_videos: set[str] = set()

    def keep(self, path: Path, key: str, *, max_side: int | None = None) -> str:
        """Register one file; a picture becomes WebP, shrunk to `max_side` px when given."""
        suffix = path.suffix.lower()
        if suffix in _AUDIO:
            key = key.removesuffix(path.suffix) + ".mp4"
        picture = suffix in _PICTURES
        if picture:
            key = key.removesuffix(path.suffix) + ".webp"
        if key in self.urls or key in self._pictures:
            return key
        if not path.is_file():
            raise FileNotFoundError(f"Grading media is missing: {path}")
        if picture:
            self._pictures[key] = (path, max_side)
            return key
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        if digest in self._videos:
            self.urls[key] = self._videos[digest]
            if digest in self._reencoded_videos:
                self.reencoded.append(key)
            return key
        too_big = path.stat().st_size > 15_000_000
        reencode = too_big and suffix in (".mp4", ".mov", ".webm")
        # A copy keeps its own container's suffix; music and re-encodes become MP4.
        kept = ".mp4" if suffix in _AUDIO or reencode else suffix
        target = self.output / UPLOADS / f"v{len(self._videos) + 1:03d}{kept}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if suffix in _AUDIO:
            # The host refuses audio-only MP4s: give the music a tiny still black picture.
            _ffmpeg(
                *("-f", "lavfi", "-i", "color=c=black:s=16x16:r=1", "-i", path),
                *("-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-tune", "stillimage"),
                *("-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"),
                *("-movflags", "+faststart", target),
            )
        elif reencode:
            self._reencode(path, target)
            self.reencoded.append(key)
            self._reencoded_videos.add(digest)
        else:
            shutil.copyfile(path, target)
        if target.stat().st_size > 15_000_000:
            raise ValueError(f"Grading asset {key} is over the Artifact's 15 MB file limit")
        self._videos[digest] = self.urls[key] = target.relative_to(self.output).as_posix()
        return key

    @staticmethod
    def _reencode(path: Path, target: Path) -> None:
        probe = subprocess.run(
            [
                os.environ.get("FFPROBE", "ffprobe"),
                *("-v", "error", "-show_entries", "format=duration"),
                *("-of", "default=noprint_wrappers=1:nokey=1", str(path)),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        bitrate = max(128, int(13_000_000 * 8 / float(probe.stdout) / 1000) - 96)
        _ffmpeg(
            *("-i", path, "-c:v", "libx264", "-preset", "medium", "-crf", "20"),
            *("-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k"),
            *("-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", target),
        )

    def _pack_pictures(self) -> None:
        """Encode every picture on all cores, then write them into bundles in page order;
        pictures with the same bytes are kept once."""
        jobs = [
            (str(path), side, str(self.cache) if self.cache else None)
            for path, side in self._pictures.values()
        ]
        if len(jobs) > 1:
            with ProcessPoolExecutor() as pool:
                encoded = list(pool.map(_encode_picture, jobs, chunksize=4))
        else:
            encoded = [_encode_picture(job) for job in jobs]
        seen: dict[str, list[Any]] = {}
        chunks: list[str] = []
        size = 0

        def close() -> None:
            nonlocal chunks, size
            if chunks:
                bundle = self.output / UPLOADS / f"p{len(self.packs) + 1:02d}.txt"
                bundle.parent.mkdir(parents=True, exist_ok=True)
                bundle.write_text("".join(chunks), encoding="ascii")
                self.packs.append(bundle.relative_to(self.output).as_posix())
            chunks, size = [], 0

        for key, data in zip(self._pictures, encoded, strict=True):
            digest = hashlib.sha1(data).hexdigest()
            if digest not in seen:
                text = base64.b64encode(data).decode("ascii")
                if len(text) > PACK_CHARS:
                    raise ValueError(f"Grading picture {key} is over the bundle size limit")
                if size + len(text) > PACK_CHARS:
                    close()
                seen[digest] = [len(self.packs), size, len(text), "image/webp"]
                chunks.append(text)
                size += len(text)
            self.urls[key] = seen[digest]
        close()

    def finish(self) -> None:
        """Pack the pictures and write `uploads.json`: the exact uploads, in Artifact calls."""
        self._pack_pictures()
        videos = sorted(set(self._videos.values()))
        files = self.packs + videos
        total = sum((self.output / name).stat().st_size for name in files)
        if total > 1024**3 or len(files) > 5000:
            raise ValueError("Grading media exceeds the Artifact's 1 GiB / 5,000-file asset limit")

        def shown(name: str) -> str:
            # Absolute, so the uploads work from whatever folder the session runs in.
            return str((self.output / name).resolve())

        write_json(
            self.output / "uploads.json",
            {
                "files": len(files),
                "bytes": total,
                # Text files go up one per call (`file_path`); videos in `file_paths` batches.
                "single": [shown(name) for name in self.packs],
                "batches": [
                    [shown(name) for name in videos[i : i + UPLOAD_BATCH]]
                    for i in range(0, len(videos), UPLOAD_BATCH)
                ],
                "names": {shown(name): name for name in files},
            },
        )


def _legacy_inputs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split(": ", 1) for line in path.read_text().splitlines() if ": " in line)


def _scenario(store: Store, run: str, key: str, media: Media, side: str) -> dict[str, Any]:
    export = store.export_dir(run, key)
    base = export / key if (export / key).is_dir() else export
    meta = read_json(base / "regression.json")
    identity = meta.get("scenario", {})
    ad_meta = read_json(base / "ad.json")
    answers = store.read_answers(run, key)
    results = read_json(export / "results.json")
    first_reencoded = len(media.reencoded)

    def asset(relative: str) -> str:
        return media.keep(base / relative, f"{side}/{key}/{relative}")

    scenes = meta.get("scenes") or [
        {"number": int(folder.name.split("-")[1]), **_legacy_inputs(folder / "inputs.txt")}
        for folder in sorted(base.glob("scene-*"))
        if folder.is_dir() and folder.name.split("-")[-1].isdigit()
    ]
    out_scenes = []
    taps: list[str] = []
    for scene in scenes:
        number = int(scene["number"])
        where = base / f"scene-{number}"
        result = results.get(f"{key}:{number}", {})
        kept = result.get("kept") or {}
        picture = scene.get("picture") or next(
            (str(p.relative_to(base)) for p in sorted(where.glob("start-picture.*"))), None
        )
        clips = []
        tries = result.get("tries") or [
            {"pk": n + 1, "remake": None if p.stem == "first" else int(p.stem.split("-")[1])}
            for n, p in enumerate(sorted(where.glob("*.mp4")))
        ]
        for i, clip in enumerate(tries):
            remake = clip.get("remake")
            filename = "first.mp4" if remake is None else f"remake-{remake}.mp4"
            relative = f"scene-{number}/{filename}"
            current = clip.get("pk") == kept.get("pk") if kept else relative == scene.get("clip")
            grade_id = f"{key}__s{number}__clip" if current else f"{key}__s{number}__take{i + 1}"
            taps.append(grade_id)
            ruling = clip.get("ruling") or {}
            file = asset(relative)
            clips.append(
                {
                    "pk": clip.get("pk", i + 1),
                    "broll": True,
                    "video": file,
                    "file": file,
                    "seconds": clip.get("seconds"),
                    "verdict": ruling.get("verdict"),
                    "inAd": current,
                    "checks": ruling.get("checks"),
                    "why": None,
                    "changed": None,
                    "reason": None,
                    "prev": None,
                    "newPic": False,
                    "sent": {"prompt": (clip.get("sent") or {}).get("prompt"), "video": {}},
                    "make": None,
                    "form": None,
                    "failures": [],
                    "ruling": ruling,
                    "line": "",
                    "gradeId": grade_id,
                }
            )
        start_pics: list[dict[str, Any]] = (
            [
                {
                    "file": asset(picture),
                    "label": "Kept starting picture",
                    "checks": [],
                    "status": None,
                    "ask": None,
                    "image": None,
                    "kept": True,
                }
            ]
            if picture
            else []
        )
        if picture:
            taps.append(f"{key}__s{number}__picture")
        out_scenes.append(
            {
                "n": number,
                "part": scene.get("part", ""),
                "kind": result.get("kind", "broll"),
                "kind_why": "",
                "broll": "",
                "line": scene.get("line", scene.get("LINE", result.get("line", ""))),
                "shows": scene.get("shows", scene.get("SHOWS", result.get("shows", ""))),
                "gave": bool(result.get("gave_up")),
                "gaves": [result["gave_up"]] if result.get("gave_up") else [],
                "run": None,
                "questions": [],
                "at": None,
                "startPics": start_pics,
                "picture": start_pics[0]["file"] if start_pics else None,
                "picked": [],
                "clips": clips,
                "agent": [],
            }
        )
    if out_scenes:
        taps.append(f"{key}__plan")
    finished_ad = next(
        (asset(name) for name in ("ad-latest.mp4", "ad.mp4") if (base / name).exists()), None
    )
    if finished_ad:
        taps.append(f"{key}__ad")
    page_files = meta.get("page_files", [])
    page_text = "\n\n".join(
        (base / name).read_text() for name in page_files if Path(name).suffix in (".txt", ".md")
    )
    if page_files:
        taps.append(f"{key}__page")
    page_read = _page_read(store, run, key, meta, base, page_files, asset, media, side)
    notes = []
    for number, note in enumerate(meta.get("notes", []), 1):
        if not note.get("after"):
            continue
        n = note.get("number", number)
        notes.append(
            {
                "number": n,
                "text": note.get("text", ""),
                "before": asset(note["before"]) if note.get("before") else None,
                "after": asset(note["after"]),
            }
        )
        taps.append(f"{key}__note{n}")
    photos = []
    question = answers.get("answers", {}).get("page_photos_this_product", {})
    labels = store.read_photo_labels().get(key, {})
    for item in question.get("items", []):
        if item.get("verdict") != "needs_label":
            continue
        if item["item"].removeprefix("photo-") in labels:
            continue
        file = item.get("file")
        if not file:
            raise ValueError(f"{key}: unlabeled photo {item['item']} has no saved file")
        photo_key = media.keep(store.media_dir(run, key) / file, f"{side}/{key}/photo/{file}")
        photos.append({"id": f"{key}__{item['item']}", "file": photo_key})
        taps.append(f"{key}__{item['item']}")
    ad: dict[str, Any] = {
        "key": key,
        "run": run,
        "kind": identity.get("kind", "full" if finished_ad else "picture"),
        "cost": answers.get("cost_usd", 0),
        "url": identity.get("link", ad_meta.get("product_url", "")),
        "trace": None,
        "session": "",
        "app": None,
        "final": "",
        "ad": finished_ad,
        "adNote": None,
        "reds": [],
        "gaveup": [],
        "watch": [],
        "answers": [],
        "scenes": out_scenes,
        "stats": {"reds": 0, "gaveup": 0, "pass": 0, "fail": 0, "unsure": 0},
        "r7": None,
        "shared": [],
        "library": [],
        "videos": [],
        "costs": [],
        "warnings": [],
        "chat": [],
        "producer": [],
        "product": "",
        "colour": "",
        "size": "",
        "behaviour": "",
        "pageText": page_text,
        "hasPage": bool(page_files),
        "pageRead": page_read,
        "notes": notes,
        "photos": photos,
        "tapIds": taps,
        "code": answers.get("answers", {}),
        "grades": store.read_grades(run),
    }
    if rich := read_json(base / "grading-records.json"):
        from .grading_adapter import enrich

        def raw_asset(file: str) -> str:
            return media.keep(store.media_dir(run, key) / file, f"{side}/{key}/raw/{file}")

        enrich(ad, rich, raw_asset)
    for scene in ad["scenes"]:
        # Clip archive numbers are at most the clip count. Continue that numeric range
        # for picture versions so case filing can keep its existing archive-doc pattern.
        archive_number = len(scene["clips"])
        for picture in scene["startPics"]:
            if not picture.get("kept"):
                archive_number += 1
                picture["gradeId"] = f"{key}__s{scene['n']}__take{archive_number}"
                taps.append(picture["gradeId"])
    ad["reencoded"] = len(media.reencoded) - first_reencoded
    if judge_answers := store.read_judge_answers(run):
        from evals.judge.trust import trusted_questions

        _judge_findings(ad, judge_answers.get(key, {}), trusted_questions())
    return ad


def _judge_findings(ad: dict[str, Any], answers: dict[str, Any], trusted: frozenset[str]) -> None:
    """Attach saved observations and the shared decision about which media needs watching."""
    from evals.judge.records import tap_doc_id
    from evals.judge.visibility import item_is_shown

    from .questions import Stage, judge_questions

    def findings(stage: Stage, item: str) -> list[dict[str, Any]]:
        return [
            {
                "id": question.id,
                "question": question.words,
                "docId": tap_doc_id(ad["key"], stage, item, question.id),
                **answers.get(question.id, {}).get(item, {}),
            }
            for question in judge_questions(stage)
        ]

    ad["judge"] = findings("ad", "ad") if ad["ad"] else []
    for scene in ad["scenes"]:
        item = f"s{scene['n']}"
        presenter = scene["kind"] != "broll"
        if scene["picture"]:
            scene["pictureJudge"] = findings("pictures", item)
            scene["showPicture"] = item_is_shown(
                answers, "pictures", item, trusted, presenter=presenter
            )
        for clip in scene["clips"]:
            if clip["inAd"]:
                clip["judge"] = findings("clips", item)
                clip["show"] = item_is_shown(answers, "clips", item, trusted, presenter=presenter)
    for finding in [
        *ad["judge"],
        *(finding for scene in ad["scenes"] for finding in scene.get("pictureJudge", [])),
        *(
            finding
            for scene in ad["scenes"]
            for clip in scene["clips"]
            for finding in clip.get("judge", [])
        ),
    ]:
        if finding.get("verdict") == "fail":
            ad["tapIds"].append(finding["docId"])


# Pictures are small on the page; a tap opens the full-size file (the skill's lightbox).
_STYLE = """
figure img{width:auto;max-width:100%;max-height:200px;object-fit:contain;justify-self:start}
.pics{grid-template-columns:repeat(auto-fill,minmax(min(100%,130px),1fr))}
.readcols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:16px}
.readcols>*{min-width:0;overflow-wrap:anywhere}
.readcols pre{max-height:60vh;overflow:auto;white-space:pre-wrap}
.dropped li{margin:2px 0}.why{font-weight:600;margin:10px 0 4px}
.judge-finding{margin:12px 0;padding:12px;border:1px solid var(--line);border-radius:10px}
.judge-finding .grade{flex-wrap:wrap}.judge-finding h4{margin:0 0 8px}
[data-note-case] video{max-height:60vh}.note-tap{margin:8px 0}.note-tap h4{margin:0 0 6px}
.judge-finding .grade button[aria-pressed="true"],.note-tap .grade button[aria-pressed="true"]{
background:var(--accent);color:var(--panel);border-color:var(--accent)}
"""


def _saved_download(store: Store, key: str, urls: Sequence[str]) -> Path | None:
    """The saved page's own copy of a picture, at the first of `urls` it downloaded."""
    folder = store.page_dir(key)
    manifest = read_json(folder / "manifest.json")
    downloads = manifest.get("downloads", {}) if isinstance(manifest, dict) else {}
    for url in urls:
        entry = downloads.get(url)
        if isinstance(entry, dict) and entry.get("file"):
            path = folder / str(entry["file"])
            if path.is_file() and path.suffix.lower() in _PICTURES:
                return path
    return None


def _page_read(
    store: Store,
    run: str,
    key: str,
    meta: Mapping[str, Any],
    base: Path,
    page_files: Sequence[str],
    asset: Any,
    media: Media,
    side: str,
) -> dict[str, Any]:
    """What the page read kept beside what it dropped, with the app's recorded reasons."""
    record = meta.get("page_read") or {}
    kept_photos = (
        [
            media.keep(store.media_dir(run, key) / file, f"{side}/{key}/raw/{file}")
            for file in record["kept_photos"]
        ]
        if "kept_photos" in record
        else [asset(name) for name in page_files if Path(name).suffix.lower() in _PICTURES]
    )
    dropped_photos = []
    for photo in record.get("photos", []):
        if photo.get("kept"):
            continue
        path = _saved_download(store, key, photo.get("urls", []))
        dropped_photos.append(
            {
                "file": media.keep(path, f"{side}/{key}/dropped/{path.name}", max_side=DROPPED_SIDE)
                if path
                else None,
                "url": (photo.get("urls") or [""])[0],
                "alt": photo.get("alt", ""),
                "why": photo.get("why", ""),
            }
        )
    text = record.get("text") or {}
    return {
        "recorded": bool(record),
        "keptText": text.get("kept", ""),
        "droppedText": text.get("dropped", []),
        "keptPhotos": kept_photos,
        "droppedPhotos": dropped_photos,
        "videos": record.get("videos", []),
        "notes": record.get("notes", []),
    }


def _page_dir(store: Store, run: str, output: Path | None) -> Path:
    return output or store.run_dir(run) / "grading-page"


def build_page(
    store: Store,
    run: str,
    *,
    output: Path | None = None,
    only: Sequence[str] | None = None,
    cache: Path | None = None,
) -> Path:
    """Build using saved files only; no app, model call or network is needed. `only` builds a
    page for some of the run's scenarios, so a large run fits the host's limits in parts.
    `cache` keeps encoded pictures between builds."""
    from .grading_notes import NOTE_TAP_CHOICES, is_note_run, note_case, run_keys

    record = store.read_run(run)
    if not record:
        raise ValueError(f"No saved run {run!r}")
    notes = is_note_run(record)
    keys = run_keys(record)
    if only:
        unknown = sorted(set(only) - set(keys))
        if unknown:
            kind = "cases" if notes else "scenarios"
            raise ValueError(f"Not {kind} of run {run!r}: {', '.join(unknown)}")
        keys = [key for key in keys if key in only]
    target = _page_dir(store, run, output)
    target.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target / UPLOADS, ignore_errors=True)
    media = Media(target, cache=cache)
    ads = {}
    if notes:
        # Read as saved: the change check's N1 answers sit beside the clip questions.
        judge_answers = read_json(store.run_dir(run) / "judge-answers.json", None)
        results = record.get("results", {})
        for key in keys:
            ads[key] = note_case(store, run, key, media, results.get(key, {}), judge_answers)
    else:
        baselines = store.read_baselines()
        for key in keys:
            ad = _scenario(store, run, key, media, "new")
            old = baselines.get(key)
            ad["old"] = _scenario(store, old, key, media, "old") if old and old != run else None
            if ad["old"]:
                # "No worse" compares two runs: a first run has nothing to compare against.
                ad["tapIds"].insert(0, f"{key}__no-worse")
            ads[key] = ad
    media.finish()
    data = {
        "run": run,
        "kind": "notes" if notes else "regression",
        "storageKey": f"regression-{run}",
        "media": media.urls,
        "packs": media.packs,
        "ads": ads,
        "grades": store.read_grades(run),
    }
    tap_ids = [doc_id for ad in ads.values() for doc_id in ad["tapIds"] if _is_tap_doc(doc_id)]
    tap_ids.extend(doc_id for doc_id in data["grades"] if _is_tap_doc(doc_id))
    if tap_ids:
        from evals.judge.records import TAP_WORDS

        data["tapFields"] = dict.fromkeys(tap_ids, "tap")
        data["tapChoices"] = {"tap": TAP_WORDS}
    if notes:
        # Both note taps share the `tap` field with the judge's, so their yes/no words travel
        # apart; the page swaps them in for the two note taps.
        data["noteTapChoices"] = NOTE_TAP_CHOICES
    # The page as built, pointing at local files: `apply_uploads` relinks it from this.
    write_json(target / "page-data.json", data)
    write_json(
        target / "publish.json",
        {
            "file_path": "grading.html",
            "capabilities": {"db": {}, "assets": {}},
            "privacy": "private",
            "uploads": "uploads.json",
            "reencoded": media.reencoded,
        },
    )
    return _render(target, data)


def _render(target: Path, data: Mapping[str, Any]) -> Path:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    # Keep the skill's renderer and autosave code. Its old round-specific shell is replaced
    # by the regression extension; its rendering helpers continue to have one source.
    renderer = (SKILL / "page.js").read_text().split("/* shell */")[0]
    renderer = renderer.replace('"r8-drafts"', 'DATA.storageKey+"-drafts"').replace(
        '"r8-view"', 'DATA.storageKey+"-view"'
    )
    renderer = renderer.replace(
        "AI summary of the judge's record. Not a grade.", "Summary from app records, not a grade."
    ).replace(
        "AI summary of the two prompts. Not a grade.", "Changes recorded by the app, not a grade."
    )
    extension = Path(__file__).with_name("grading.js").read_text()
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(data["run"])} regression grading</title>
<style>{(SKILL / "page.css").read_text()}{_STYLE}</style></head><body>
<div class="shell"><nav class="rail" id="rail" aria-label="Scenarios"></nav>
<main id="view"></main></div>
<script type="application/json" id="data">{blob}</script>
<script>{renderer}\n{extension}</script></body></html>"""
    path = target / "grading.html"
    atomic_write_text(path, page)
    return path


# The Artifact tool's upload results as session transcripts record them (Nov 2026 wording):
# one text file reads `Asset uploaded: "<name>" (<n> bytes, <type>, sha256 <hex>) is now
# stored ... as "/_blob/<id>"`; a batch lists `"<path>" (<n> bytes, <type>) → "/_blob/<id>"`.
_SINGLE_RESULT = re.compile(
    r'Asset uploaded: "[^"\n]+" \(\d+ bytes, [^,)]+, sha256 ([0-9a-f]{64})\)'
    r'[^"]*"(/_blob/[0-9a-f]{32})"'
)
_BATCH_RESULT = re.compile(r'"([^"\n]+)" \((\d+) bytes, [^)]*\) → "(/_blob/[0-9a-f]{32})"')


def _artifact_id(url: str) -> str | None:
    match = re.search(r"artifact/([\w-]+)", url)
    return match.group(1) if match else None


def _upload_results(transcripts: Sequence[Path], url: str) -> Iterator[str]:
    """The text of every upload result in the transcripts, oldest first, for calls to `url`
    only: the same file sent to another page gets that page's id."""
    page = _artifact_id(url)
    if not page:
        raise ValueError(f"Not an Artifact link: {url!r}")
    files = [p for t in transcripts for p in (list(t.rglob("*.jsonl")) if t.is_dir() else [t])]
    files.sort(key=lambda file: file.stat().st_mtime)
    for file in files:
        calls: set[str] = set()
        for line in file.read_text(errors="replace").splitlines():
            if "tool_" not in line:
                continue
            try:
                content = json.loads(line).get("message", {}).get("content")
            except json.JSONDecodeError, AttributeError:
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    call = block.get("input") or {}
                    if call.get("asset") and _artifact_id(str(call.get("url", ""))) == page:
                        calls.add(str(block.get("id")))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    result = block.get("content")
                    yield (
                        result
                        if isinstance(result, str)
                        else "\n".join(
                            str(part.get("text", ""))
                            for part in result or []
                            if isinstance(part, dict)
                        )
                    )


def apply_uploads(target: Path, transcripts: Sequence[Path], url: str) -> tuple[Path, int]:
    """Point the built page at its uploaded files, read from the upload results the session
    transcripts hold for this page; bundles match by SHA-256, videos by name and size."""
    data = read_json(target / "page-data.json")
    if not data:
        raise ValueError(f"No built page in {target}: build it first")
    names = list(data["packs"]) + sorted(
        {v for v in data["media"].values() if isinstance(v, str) and v.startswith(UPLOADS + "/")}
    )
    by_digest = {}
    by_name = {}
    for name in names:
        path = target / name
        by_digest[hashlib.sha256(path.read_bytes()).hexdigest()] = name
        by_name[(path.name, path.stat().st_size)] = name
    found: dict[str, str] = {}
    for text in _upload_results(transcripts, url):
        for digest, blob in _SINGLE_RESULT.findall(text):
            if digest in by_digest:
                found[by_digest[digest]] = blob
        for shown, size, blob in _BATCH_RESULT.findall(text):
            if (Path(shown).name, int(size)) in by_name:
                found[by_name[(Path(shown).name, int(size))]] = blob
    missing = [name for name in names if name not in found]
    if missing:
        raise ValueError(
            f"{len(missing)} of {len(names)} files have no upload result for {url} yet: "
            + ", ".join(missing)
        )
    linked = {
        **data,
        "packs": [found[name] for name in data["packs"]],
        "media": {
            k: found.get(v, v) if isinstance(v, str) else v for k, v in data["media"].items()
        },
    }
    return _render(target, linked), len(found)


def import_grades(store: Store, run: str, source: Path) -> int:
    """File an exported Artifact grades collection, keeping taps and words verbatim."""
    record = store.read_run(run)
    if not record:
        raise ValueError(f"No saved run {run!r}")
    if source.is_dir():
        folder = source / "grades" if (source / "grades").is_dir() else source
        raw = {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))}
    else:
        raw = json.loads(source.read_text())
    if isinstance(raw, list):
        raw = {doc["id"]: doc.get("data", doc) for doc in raw}
    if isinstance(raw, dict) and "grades" in raw:
        raw = raw["grades"]
    if not isinstance(raw, dict):
        raise ValueError("Grades must be a collection of document ids and grade/note/at records")
    from .grading_notes import parse_note_tap_id, run_keys, validate_note_tap

    grades = store.read_grades(run)
    labels = store.read_photo_labels()
    accepted = {}
    keys = run_keys(record)
    for doc_id, wrapped in raw.items():
        scenario, separator, suffix = doc_id.partition("__")
        if not separator or scenario not in keys:
            if doc_id != "round__notes":
                raise ValueError(f"Grade {doc_id!r} does not belong to run {run!r}")
        doc = wrapped.get("data", wrapped) if isinstance(wrapped, dict) else None
        if parse_note_tap_id(doc_id):
            if (note_tap := validate_note_tap(doc_id, doc)) is None:
                # Words saved before a tap are not a grade, as for the judge's taps.
                grades.pop(doc_id, None)
            else:
                accepted[doc_id] = note_tap
            continue
        if _is_judge_tap(doc_id):
            from evals.judge.records import JudgeTap

            if isinstance(doc, dict) and doc.get("tap") is None:
                # A note may autosave before its tap. It is not a human verdict.
                if set(doc) != {"tap", "note", "at"} or not all(
                    isinstance(doc[field], str) for field in ("note", "at")
                ):
                    raise ValueError(f"Grade {doc_id!r}: expected tap, note and at")
                grades.pop(doc_id, None)
                continue
            accepted[doc_id] = JudgeTap.model_validate(doc).model_dump()
            continue
        if (
            not isinstance(doc, dict)
            or set(doc) != {"grade", "note", "at"}
            or doc["grade"] not in (None, "pass", "fail")
            or not isinstance(doc["note"], str)
            or not isinstance(doc["at"], str)
        ):
            raise ValueError(f"Grade {doc_id!r}: expected grade (pass/fail/null), note and at")
        accepted[doc_id] = doc
        if suffix.startswith("photo-"):
            digest = suffix.removeprefix("photo-")
            if not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError(f"Grade {doc_id!r}: photo needs its SHA-256 hash")
            if doc["grade"] is not None:
                labels.setdefault(scenario, {})[digest] = (
                    "this" if doc["grade"] == "pass" else "not"
                )
    grades.update(accepted)
    lines = [f"# {run}: Julian's grades", ""]
    for doc_id, doc in sorted(grades.items()):
        if "tap" in doc:
            from evals.judge.records import TAP_WORDS

            words = TAP_WORDS.get(doc["tap"], doc["tap"])
        else:
            words = doc["grade"] or "no tap"
        lines.extend([f"## {doc_id} [{words}]", doc["note"], ""])
    store.write_grades(run, grades)
    store.write_grades_markdown(run, "\n".join(lines))
    store.write_photo_labels(labels)
    return len(accepted)


def _is_judge_tap(doc_id: str) -> bool:
    from evals.judge.records import parse_tap_doc_id

    return parse_tap_doc_id(doc_id) is not None


def _is_tap_doc(doc_id: str) -> bool:
    """A `{tap, note, at}` document: the judge's three taps or a note run's yes/no taps."""
    from .grading_notes import parse_note_tap_id

    return _is_judge_tap(doc_id) or parse_note_tap_id(doc_id) is not None


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--import-grades", type=Path, help="File the exported grades collection")
    parser.add_argument("--output", type=Path, help="Page output folder (default: in the run)")
    parser.add_argument(
        "--only",
        help="Comma-separated scenario keys (case ids on a note run): build a page for part "
        "of the run (use with --output)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "adforge-grading-pictures",
        help="Encoded-picture cache folder",
    )
    parser.add_argument(
        "--apply-uploads",
        type=Path,
        nargs="+",
        metavar="TRANSCRIPT",
        help="Session transcripts (files or folders) holding the uploads: relink the built page",
    )
    parser.add_argument("--url", help="The published page's Artifact link (with --apply-uploads)")
    args = parser.parse_args(argv)
    try:
        if args.apply_uploads:
            if not args.url:
                raise ValueError("--apply-uploads needs --url, the page the files were sent to")
            target = _page_dir(store, args.run, args.output)
            path, count = apply_uploads(target, args.apply_uploads, args.url)
            print(f"{path}: {count} uploaded files linked")
        elif args.import_grades is not None:
            count = import_grades(store, args.run, args.import_grades)
            print(f"{args.run}: saved {count} grade documents and photo labels")
        else:
            only = (
                [key.strip() for key in args.only.split(",") if key.strip()] if args.only else None
            )
            path = build_page(store, args.run, output=args.output, only=only, cache=args.cache)
            plan = read_json(path.parent / "uploads.json")
            print(path)
            print(
                f"{plan['files']} files to upload ({plan['bytes'] / 1e6:.0f} MB): "
                f"{len(plan['single'])} bundles one per call, {len(plan['batches'])} video "
                f"batches: {path.parent / 'uploads.json'}"
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
