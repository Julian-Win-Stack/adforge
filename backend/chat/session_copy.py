"""Make a copy of a finished session (#566), so the shop owner can try notes on the copy and
keep the original as it was.

A session's rows spread over every table that links to it, directly or through another
table (MODELS). The copy is made in one transaction: every row gets a new id, and every link
between the session's rows, by a field or by a row id kept inside JSON (IDS_IN_JSON), is
pointed at the copy's row. Files are not copied: the copy names the same file-store keys,
which is safe as the store never overwrites a key (adforge/file_store.py:save)."""

import uuid
from collections import defaultdict
from collections.abc import Iterator
from functools import cache
from typing import Any, cast

from django.db import models, transaction

from agents.models import PlannerQuestion, RefusedPicture, SystemNote, ToolCall
from gateway.models import ModelCall, SystemPrompt
from jobs import notices
from jobs.models import (
    AgentFix,
    AgentFixTry,
    Job,
    LibraryItem,
    OwnerQuestion,
    ProducedItem,
    ProductPhoto,
    ProductVideo,
    ReferencePhoto,
    Scene,
    SceneAgentRun,
    SceneStep,
)

from .models import Attachment, Message, Session

Model = type[models.Model]

# Every table whose rows belong to a session: those that link to a session, or to one of
# these tables' rows. The guard test fails when a table is added that isn't here.
MODELS: tuple[Model, ...] = (
    Session,
    Message,
    Attachment,
    ToolCall,
    SystemNote,
    RefusedPicture,
    PlannerQuestion,
    SystemPrompt,
    ModelCall,
    Job,
    ProductPhoto,
    ProductVideo,
    LibraryItem,
    Scene,
    ReferencePhoto,
    SceneStep,
    SceneStep.reference_photos.through,
    ProducedItem,
    AgentFix,
    SceneAgentRun,
    AgentFixTry,
    OwnerQuestion,
)

# Where a row id is kept inside a JSON field: each path to it, a key or "*" for every item of
# a list, and the table it names. A sent list's "call" is the tool call's id as text.
Path = tuple[str, ...]
_SENT: list[tuple[Path, Model]] = [
    (("run",), SceneAgentRun),
    (("scene_run",), SceneAgentRun),
    (("edited_from",), ProducedItem),
    (("compared_with",), ProducedItem),
    (("call",), ToolCall),
]
_RULING: list[tuple[Path, Model]] = [
    (("compared_with",), ProducedItem),
    (("owner_check", "message"), Message),
]
IDS_IN_JSON: dict[tuple[Model, str], list[tuple[Path, Model]]] = {
    (ProducedItem, "cuts"): [(("*", "clip"), ProducedItem)],
    (ProducedItem, "sent"): _SENT,
    (ProducedItem, "ruling"): _RULING,
    (AgentFix, "ruling"): _RULING,
    (AgentFixTry, "sent"): _SENT,
    (SceneAgentRun, "quotes"): [(("*", "message_id"), Message), (("*", "handed_by"), ToolCall)],
    (OwnerQuestion, "answer"): [(("delivery_scene",), Scene), (("delivery_run",), SceneAgentRun)],
}
# Every other JSON field of these tables, read and found to keep no row ids: file-store keys,
# library numbers, scene numbers, words and model answers.
NO_IDS_IN_JSON: set[tuple[Model, str]] = {
    (PlannerQuestion, "items"),
    (ToolCall, "arguments"),
    (ToolCall, "pictures"),
    (ModelCall, "handoff"),
    (ModelCall, "images"),
    (ModelCall, "output"),
    (SystemPrompt, "tools"),
    (AgentFix, "scene_before"),
    (AgentFix, "said"),
    (AgentFix, "camera_before"),
    (AgentFixTry, "scene_row"),
    (AgentFixTry, "picture_problems"),
    (AgentFixTry, "fix_check"),
    *((Job, name) for name in ("firecrawl", "colours", "side_photos", "product_photos")),
    (Job, "length_kept_script"),
    (Job, "warnings"),
    (LibraryItem, "source"),
    (LibraryItem, "cut_out"),
    *((ProducedItem, name) for name in ("words", "captions", "end_card")),
    (ProductPhoto, "copy_links"),
    (ProductVideo, "frames"),
    (ProductVideo, "watched"),
    *(
        (Scene, name)
        for name in (
            "needs",
            "scene_pictures",
            "piece",
            "excluded",
            "owner_part",
            "owner_part_drawn",
            "fact_problems",
            "owner_words",
            "shortened_from",
        )
    ),
    (SceneAgentRun, "scene_before"),
    *(
        (SceneStep, name)
        for name in ("needs", "owner_part", "scene_pictures", "piece", "excluded", "pictures_sent")
    ),
}

# Rows are read and written this many at a time, so a long session's model calls never sit in
# memory all at once.
CHUNK = 500


class StillWorking(Exception):
    """The session is still making something, so it isn't copied: the reason, in plain words."""


def rows_of(session: Session) -> dict[Model, set[Any]]:
    """The ids of every row that belongs to `session`, by table: the session, then every row
    linking to one of them, until no more are found."""
    found: dict[Model, set[Any]] = defaultdict(set)
    found[Session].add(session.pk)
    grew = True
    while grew:
        grew = False
        for model in MODELS:
            for field in _links(model):
                targets = found.get(field.related_model)  # type: ignore[arg-type]
                if not targets:
                    continue
                more = set(
                    model._base_manager.filter(**{f"{field.attname}__in": targets})
                    .exclude(pk__in=found[model])
                    .values_list("pk", flat=True)
                )
                if more:
                    found[model] |= more
                    grew = True
    return {model: pks for model, pks in found.items() if pks}


def still_working(session: Session) -> str | None:
    """Why `session` can't be copied yet, in plain words, or None when nothing is running."""
    if session.producer_running:
        return "This chat is still working on the ad. Make a copy once it has finished."
    if SceneStep.objects.filter(
        scene__job__session=session, status=SceneStep.Status.RUNNING
    ).exists():
        return "A scene is still being made. Make a copy once it has finished."
    return None


def copy(session: Session) -> Session:
    """A copy of `session`, with every row it has, idle and named "Copy of" its name. Raises
    StillWorking, with a notice kept, while anything in it is still running."""
    with transaction.atomic():
        locked = Session.objects.select_for_update().get(pk=session.pk)
        why = still_working(locked)
        if why is None:
            return _copied(locked)
    notices.record(
        locked,
        notices.COPY_REFUSED,
        f"Copying the session was refused: {why}",
        Message.Level.INFO,
    )
    raise StillWorking(why)


def _copied(session: Session) -> Session:
    rows = rows_of(session)
    new: dict[Model, dict[Any, Any]] = defaultdict(dict)
    for model in _in_order():
        for chunk in _chunks(sorted(rows.get(model, ()))):
            originals = list(model._base_manager.filter(pk__in=chunk).order_by("pk"))
            copies = [_copy_of(row, new) for row in originals]
            if model is Session:
                _named_and_idle(cast(Session, copies[0]), session.name)
            model._base_manager.bulk_create(copies)
            for row, made in zip(originals, copies, strict=True):
                new[model][row.pk] = made.pk
            _keep_times(model, originals, copies)
    outside = _relink(rows, new)
    made = Session.objects.get(pk=new[Session][session.pk])
    if outside:
        notices.record(
            made,
            notices.COPY_KEPT_OLD_IDS,
            "The copy kept row ids that name no row of the session it was copied from, as "
            f"they were: {', '.join(sorted(set(outside)))}.",
            Message.Level.INFO,
        )
    return made


def _named_and_idle(copied: Session, name: str) -> None:
    """The copy is named after the original and starts idle: no producer, not paused."""
    longest = Session._meta.get_field("name").max_length or len(name)
    copied.name = f"Copy of {name}"[:longest]
    copied.producer_running = False
    copied.producer_seen_at = None
    copied.producer_copy = None
    copied.producer_beat_at = None
    copied.paused_at = None


def _copy_of(row: models.Model, new: dict[Model, dict[Any, Any]]) -> models.Model:
    """`row`'s copy, unsaved: a new id, and each link to a row already copied pointed at its
    copy. A link to a row not copied yet is left blank until _relink."""
    made = type(row)()
    for field in type(row)._meta.concrete_fields:
        value = getattr(row, field.attname)
        if field.primary_key:
            value = uuid.uuid4() if isinstance(field, models.UUIDField) else None
        elif field in _links(type(row)):
            late = _late(type(row), field)
            value = _linked(field, value, new) if not late or value in new[_target(field)] else None
        setattr(made, field.attname, value)
    return made


def _keep_times(model: Model, originals: list[models.Model], copies: list[models.Model]) -> None:
    """Put back the times saving set to now, so the copy's rows keep their order and times."""
    stamped = [
        field.attname
        for field in model._meta.concrete_fields
        if getattr(field, "auto_now_add", False) or getattr(field, "auto_now", False)
    ]
    if not stamped or model is Session:
        return
    for row, made in zip(originals, copies, strict=True):
        for name in stamped:
            setattr(made, name, getattr(row, name))
    model._base_manager.bulk_update(copies, stamped)


def _relink(rows: dict[Model, set[Any]], new: dict[Model, dict[Any, Any]]) -> list[str]:
    """Point the links each copy left blank, and the row ids inside its JSON, at the copy's
    rows: the links to rows copied after it. Gives each row id inside JSON that names no row of
    the session, as "table.field: id", kept as it was."""
    outside: list[str] = []
    for model in MODELS:
        late = [field for field in _links(model) if _late(model, field)]
        json_fields = [name for owner, name in IDS_IN_JSON if owner is model]
        if not late and not json_fields:
            continue
        for chunk in _chunks(sorted(rows.get(model, ()))):
            originals = model._base_manager.filter(pk__in=chunk).only(
                "pk", *(field.attname for field in late), *json_fields
            )
            copies = []
            for row in originals:
                made = model(pk=new[model][row.pk])
                for field in late:
                    old = getattr(row, field.attname)
                    setattr(made, field.attname, _linked(field, old, new))
                for name in json_fields:
                    value = _remapped(getattr(row, name), IDS_IN_JSON[(model, name)], new, outside)
                    setattr(made, name, value)
                copies.append(made)
            model._base_manager.bulk_update(copies, [field.attname for field in late] + json_fields)
    return outside


def _linked(field: models.Field[Any, Any], old: Any, new: dict[Model, dict[Any, Any]]) -> Any:
    """The copy's id for the row `field` links to. Every row a session's row links to is the
    session's, so a link to any other row is a bug in what rows_of collects, never left to
    point back at the original."""
    if old is None:
        return None
    found = new[_target(field)].get(old)
    if found is None:
        raise RuntimeError(
            f"{field.model._meta.label}.{field.name} links to {old}, a row the copy didn't collect"
        )
    return found


def ids_in(value: Any, paths: list[tuple[Path, Model]]) -> Iterator[tuple[Any, Model]]:
    """Each row id kept in a JSON `value` at `paths`, with the table it names."""
    for path, target in paths:
        for found in _at(value, path):
            if found is not None:
                yield _id(found), target


def _remapped(
    value: Any,
    paths: list[tuple[Path, Model]],
    new: dict[Model, dict[Any, Any]],
    outside: list[str],
) -> Any:
    for path, target in paths:

        def changed(old: Any, target: Model = target, path: Path = path) -> Any:
            return _new_id(old, new[target], outside, f"{target._meta.label} {'.'.join(path)}")

        value = _replaced(value, path, changed)
    return value


def _new_id(old: Any, copies: dict[Any, Any], outside: list[str], where: str) -> Any:
    """The copy's id for `old`, kept in the same form: an id kept as text stays text. An id
    naming no row of the session, as a row deleted since, is kept as it is and noted in
    `outside`: it can't name one of the original's rows, as every one of them was copied."""
    found = copies.get(_id(old))
    if found is None:
        outside.append(f"{where}: {old}")
        return old
    return str(found) if isinstance(old, str) else found


def _id(value: Any) -> Any:
    return int(value) if isinstance(value, str) and value.isdigit() else value


def _at(value: Any, path: Path) -> Iterator[Any]:
    if not path:
        yield value
    elif path[0] == "*":
        if isinstance(value, list):
            for item in value:
                yield from _at(item, path[1:])
    elif isinstance(value, dict) and path[0] in value:
        yield from _at(value[path[0]], path[1:])


def _replaced(value: Any, path: Path, change: Any) -> Any:
    if not path:
        return value if value is None else change(value)
    if path[0] == "*":
        if isinstance(value, list):
            return [_replaced(item, path[1:], change) for item in value]
        return value
    if isinstance(value, dict) and path[0] in value:
        return {**value, path[0]: _replaced(value[path[0]], path[1:], change)}
    return value


def _target(field: models.Field[Any, Any]) -> Model:
    """The table `field` links to."""
    target = field.related_model
    assert isinstance(target, type)
    return target


def _links(model: Model) -> list[models.Field[Any, Any]]:
    """`model`'s fields that link to a row of a session's tables."""
    return [
        field
        for field in model._meta.concrete_fields
        if field.is_relation and field.related_model in MODELS
    ]


@cache
def _in_order() -> list[Model]:
    """The tables in an order where every link that can't be blank points at a table already
    copied, so each row's copy can be saved as it is made. A table whose every link points at
    a table already copied goes first, so links left for _relink are few."""
    ordered: list[Model] = []
    waiting = list(MODELS)
    while waiting:
        every_link = [
            model
            for model in waiting
            if all(
                field.related_model in ordered
                for field in _links(model)
                if field.related_model is not model
            )
        ]
        needed_links = [
            model
            for model in waiting
            if all(
                field.null or field.related_model in ordered
                for field in _links(model)
                if field.related_model is not model
            )
        ]
        ready = every_link or needed_links
        if not ready:
            raise RuntimeError(f"Links that can't be blank go round in a circle: {waiting}")
        ordered.append(ready[0])
        waiting.remove(ready[0])
    return ordered


def _late(model: Model, field: models.Field[Any, Any]) -> bool:
    """Whether `field` links to a table copied after `model`, or to `model` itself, so it is
    left blank until _relink."""
    order = _in_order()
    return order.index(field.related_model) >= order.index(model)  # type: ignore[arg-type]


def _chunks(pks: list[Any]) -> Iterator[list[Any]]:
    for start in range(0, len(pks), CHUNK):
        yield pks[start : start + CHUNK]
