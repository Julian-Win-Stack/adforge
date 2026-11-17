"""Where an unfinished scenario of a run carries on from when the run is started again."""

from dataclasses import dataclass
from pathlib import Path

from .store import Store, read_json

# Each save a scenario keeps, by its folder under checkpoints/, and how it is named.
_CHECKPOINTS = {
    "latest": "its last checked picture",
    "plan": "the plan checkpoint",
    "page": "the page checkpoint",
}
ENDED = "where its last attempt ended"


@dataclass(frozen=True)
class ResumePoint:
    label: str
    dump: Path
    media: Path
    session_id: str


def resume_point(store: Store, run: str, scenario: str, *, replayed: bool) -> ResumePoint | None:
    """The newest complete save this run kept of `scenario`, or None to start afresh: where
    its last attempt ended (saved even when it stopped or failed), or the last checkpoint a
    killed attempt left. A run started from a baseline holds that baseline's page and plan
    checkpoints, which are not its own, so it carries on only from its own saves."""
    folder = store.scenario_dir(run, scenario)
    found: list[tuple[float, ResumePoint]] = []
    ended = folder / "result.json"
    session = read_json(ended).get("session_id")
    dump, media = store.dump_path(run, scenario), store.media_dir(run, scenario)
    if session and dump.is_file() and media.is_dir():
        found.append((ended.stat().st_mtime, ResumePoint(ENDED, dump, media, session)))
    for name, label in _CHECKPOINTS.items():
        if replayed and name != "latest":
            continue
        saved = folder / "checkpoints" / name
        session = read_json(saved / "scenario.json").get("session_id")
        complete = (saved / "done").is_file() and (saved / "db.dump").is_file()
        if session and complete and (saved / "media").is_dir():
            point = ResumePoint(label, saved / "db.dump", saved / "media", session)
            found.append(((saved / "done").stat().st_mtime, point))
    return max(found, key=lambda entry: entry[0])[1] if found else None


def carry_on(session_id: str) -> None:
    """Start the restored chat's work again as the app does after its worker stopped: each
    tool left unfinished runs again, each running scene agent is handed out again, and the
    producer is woken."""
    from agents import tasks
    from chat.models import Session

    Session.objects.filter(pk=session_id).update(
        producer_running=True, producer_seen_at=None, producer_copy=None, producer_beat_at=None
    )
    tasks.carry_on_after_the_worker_starts()
