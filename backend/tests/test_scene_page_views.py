"""Every view of the product its page shows reaches the image model, the picture check and
the clip judge, beside the scene agent's picks, labelled from the page (round 13 root cause
1). Through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.scene_agent import page_views
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import cut_and_send
from jobs.clip_judge import JUDGE_COLOUR_RULE
from jobs.models import Job, LibraryItem, ProducedItem

from .conftest import HeldSteps, handoffs, paid_for, recorded
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

BACK = "The mug from the back, its maker's stamp under the handle."


def _a_back_view_in_red() -> tuple[LibraryItem, str]:
    """Library item 2, which the agent never picks, made the mug's back in red."""
    job = Job.objects.get()
    LibraryItem.objects.filter(job=job, number=2).update(side="back", colour="red", label=BACK)
    return LibraryItem.objects.get(job=job, number=2), cut_and_send.ad_colour(job)


def test_every_page_view_reaches_the_picture_model_its_check_and_the_clip_judge(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    back, ad = _a_back_view_in_red()
    other_colour = (
        f"This photo shows the product in red; it shows the shape only. The ad's product is {ad}."
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    # The image model gets the back in its own colour, after the agent's pick and before the
    # room, told to take only its shape.
    [draw] = handoffs("draw_scene_picture")
    assert draw["pictures"][1] == back.file
    assert (
        "Image 2: an extra view of the product from its page: The mug from the back, its "
        "maker's stamp under the handle (side: back). Take only its shape, parts and sides "
        f"from it, never its colour. {other_colour}"
    ) in draw["prompt"]
    assert "recolour" not in paid_for()
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    assert [
        (p.get("library_number"), p.get("page_view", False)) for p in picture.sent["photos"]
    ] == [
        (1, False),
        (2, True),
        (None, False),
    ]

    # Its check gets the same photos, each labelled from the page.
    check = ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")[0]
    assert check.images[2]["key"] == back.file
    pick, view, _room = check.handoff["photos"]
    assert pick.startswith("Image 1: Copy the shape and glaze. (The page photo shows: ")
    assert pick.endswith(", side: front.)")
    assert view == (
        "Extra view, page photo 2: The mug from the back, its maker's stamp under the handle "
        f"(side: back). {other_colour}"
    )
    assert page_views.CHECK_COLOUR_RULE in str(
        check.system_prompt and check.system_prompt.instructions
    )

    # The end picture's edit and check get it too.
    [edit] = handoffs("make_end_picture")
    assert edit["pictures"][-1] == back.file
    assert "Image 3: an extra view of the product from its page" in edit["prompt"]
    end_check = ModelCall.objects.filter(purpose="check_scene_picture").order_by(
        "created_at", "pk"
    )[1]
    assert end_check.handoff["photos"][-1] == view

    # The clip judge is shown it with the page's label, and judges colour without it.
    product = ModelCall.objects.filter(purpose="check_clip_product").first()
    assert product is not None
    references = product.handoff["references"]
    assert references[1].startswith(
        "Real product photo 1, sent to the picture model, not the video model: The page photo "
        "shows: "
    )
    assert references[1].endswith("What the agent asked for: Image 1: Copy the shape and glaze.")
    assert references[2] == (
        "Real product photo 2, an extra view from the page, sent to the picture model, not the "
        "video model. It shows: The mug from the back, its maker's stamp under the handle "
        f"(side: back). {other_colour}"
    )
    assert back.file in [image["key"] for image in product.images]
    assert JUDGE_COLOUR_RULE in str(product.system_prompt and product.system_prompt.instructions)


def test_a_view_without_room_is_left_out_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pick and the room fill the picture model's pictures.
    monkeypatch.setattr(page_views, "MOST_PICTURES", 2)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert len(draw["pictures"]) == 2
    assert "extra view" not in draw["prompt"]
    assert (
        "problem",
        "Scene 2's starting picture: the page's view 2 was left out, as the picture model "
        "takes 2 pictures at most.",
    ) in recorded(api, session_id)


@pytest.mark.parametrize("problem", ["missing file", "no side"])
def test_a_view_with_a_missing_file_or_no_side_is_said_in_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    problem: str,
) -> None:
    job = Job.objects.get()
    if problem == "missing file":
        LibraryItem.objects.filter(job=job, number=2).update(file="gone/never-saved.png")
    else:
        LibraryItem.objects.filter(job=job, number=2).update(side="none")
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    [check] = handoffs("check_scene_picture")
    if problem == "missing file":
        assert "gone/never-saved.png" not in draw["pictures"]
        assert "extra view" not in draw["prompt"]
        notice = (
            "Scene 2's starting picture: the page's view 2 was left out, as its file could not "
            "be found."
        )
    else:
        assert "(side: not given)" in draw["prompt"]
        assert "(side: not given)" in check["photos"][1]
        notice = (
            "Scene 2's starting picture: the page's view 2 was sent with no side, as the "
            "library gives none."
        )
    assert ("problem", notice) in recorded(api, session_id)


def test_a_photo_the_library_no_longer_holds_keeps_the_agents_words_with_a_notice(
    scene_agent_ready: None,  # noqa: F811
    api: Any,
    session_id: str,
) -> None:
    from agents.scene_agent.photos import SelectedPhoto

    job = Job.objects.get()
    gone = SelectedPhoto(file="gone.png", line="Image 1: Copy the rim.", library_number=99)
    assert page_views.check_lines(job, [gone], "green") == ["Image 1: Copy the rim."]
    assert page_views.judge_labels(job, [gone], "green") == ["Image 1: Copy the rim."]
    assert (
        "problem",
        "Library items 99 are no longer in the library, so the checks were told only the "
        "agent's words for them, not what the page photo shows.",
    ) in recorded(api, session_id)


def test_short_room_keeps_an_unshown_side_in_the_pictures_colour_first() -> None:
    from jobs.library import LibraryEntry, PagePhoto

    def view(number: int, side: str, colour: str) -> LibraryEntry:
        return LibraryEntry(
            number=number,
            file=f"{number}.png",
            source=PagePhoto(photo=number),
            kind="side",
            side=side,
            alone="yes",
            colour=colour,
            label=f"View {number}.",
        )

    views = [
        view(1, "front", "red"),
        view(2, "front", "black"),
        view(3, "back", "red"),
        view(4, "back", "black"),
        view(5, "top", "red"),
        view(6, "side", "black"),
    ]
    # The picks show the front: the black back comes first, then a side only red shows,
    # then the rest in black, then the rest.
    ordered = page_views._by_need(views, {"front"}, "black")
    assert [item.number for item in ordered] == [4, 6, 5, 2, 1, 3]
