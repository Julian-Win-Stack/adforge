"""Existing scene runs keep each kind's consumed tries across the budget split."""

from collections.abc import Callable

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from gateway.fake import FakeModel, turn
from jobs.models import SceneAgentRun

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("ends", [0, 2])
def test_existing_picture_tries_are_split_without_losing_paid_work(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    ends: int,
) -> None:
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(3)],
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(ends)],
        turn(calls=[("give_up", {"reason": "Keep these pictures."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    before = [("jobs", "0092_produced_end_card")]
    after = [("jobs", "0093_separate_picture_tries")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        old = executor.loader.project_state(before).apps.get_model("jobs", "SceneAgentRun")
        assert old.objects.get().pictures == 3 + ends
        MigrationExecutor(connection).migrate(after)
    finally:
        # Back to every migration, so later tests on this database see today's tables.
        latest = MigrationExecutor(connection)
        latest.migrate(latest.loader.graph.leaf_nodes())
    kept = SceneAgentRun.objects.get()
    assert (kept.starting_pictures, kept.end_pictures) == (3, ends)
