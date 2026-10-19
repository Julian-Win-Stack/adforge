"""The owner part on the scene record (owner-part approval, T0): a part of the product no
photo shows, drawn from the shop owner's words or invented by the planner, which the shop
owner approves in the starting picture before the scene's clip is made. The scene keeps it
and when it was approved; each step snapshots it, as it does the scene's needs. Run on the
real database: no model is called."""

from datetime import timedelta

import pytest
from django.utils import timezone

from agents.models import ToolCall
from chat.models import Session
from jobs import owner_parts
from jobs.models import Job, ProducedItem, ReferencePhoto, Scene, SceneStep

pytestmark = pytest.mark.django_db

APPLICATOR = {
    "what": "the applicator",
    "source": "owner's words",
    "looks": "A soft white tip on a thin clear stem.",
}


@pytest.fixture
def call() -> ToolCall:
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/products/tint")
    return ToolCall.objects.create(
        session=session,
        job=job,
        agent="producer",
        tool="make_starting_picture",
        call_id="c0",
        arguments={},
    )


def a_broll_scene(call: ToolCall, number: int = 2, **fields: object) -> Scene:
    assert call.job is not None
    return Scene.objects.create(
        job=call.job,
        number=number,
        line="One dab and it lasts all day.",
        shows="A hand dabs the tint on a cheek.",
        broll_kind=Scene.BrollKind.DOES_A_JOB,
        person_shown=Scene.PersonShown.NO_FACE,
        usage="Dab it on, blend it in.",
        **fields,
    )


def a_picture(scene: Scene, call: ToolCall, version: int = 1) -> ProducedItem:
    """A starting picture drawn for `scene` as it stands now."""
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.STARTING_PICTURE,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        **SceneStep.snapshot(scene),
    )
    return ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=version,
        file=f"s{scene.number}_v{version}.png",
    )


def test_a_scene_has_no_owner_part_unless_the_plan_gives_it_one(call: ToolCall) -> None:
    scene = a_broll_scene(call)

    assert scene.owner_part is None
    assert scene.owner_part_approved_at is None
    assert owner_parts.owner_part(scene) is None


def test_the_owner_part_reads_back_as_its_three_fields(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    scene.refresh_from_db()

    assert owner_parts.owner_part(scene) == owner_parts.OwnerPart(
        what="the applicator",
        source="owner's words",
        looks="A soft white tip on a thin clear stem.",
    )


def test_an_owner_part_has_no_empty_field() -> None:
    with pytest.raises(ValueError):
        owner_parts.OwnerPart(what=" ", source="invented", looks="A thin clear stem.")
    with pytest.raises(ValueError):
        owner_parts.OwnerPart(what="the applicator", source="invented", looks="")
    with pytest.raises(ValueError):
        owner_parts.OwnerPart.model_validate(
            {"what": "the applicator", "source": "a guess", "looks": "A thin clear stem."}
        )


def test_a_step_snapshots_the_owner_part_as_it_does_the_needs(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)

    picture = a_picture(scene, call)

    assert picture.step is not None
    assert picture.step.owner_part == APPLICATOR
    # The picture is the scene's current one while the part stays as it was drawn with...
    assert scene.current_picture() == picture
    # ...and stops being it once the shop owner's words change how the part looks.
    scene.owner_part = {**APPLICATOR, "looks": "A pink foam tip on a short gold stem."}
    scene.save()
    assert scene.current_picture() is None


def test_a_scene_without_an_owner_part_still_finds_its_current_picture(call: ToolCall) -> None:
    scene = a_broll_scene(call)

    picture = a_picture(scene, call)

    assert picture.step is not None
    assert picture.step.owner_part is None
    assert scene.current_picture() == picture


def test_a_scene_that_stops_showing_anything_drops_its_owner_part(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)

    scene.change_line("One dab and it lasts all day.", "")

    assert scene.owner_part is None


def test_the_owner_part_is_not_approved_until_the_shop_owner_says_yes(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call)

    assert not owner_parts.is_approved(scene)


def test_the_owner_part_is_approved_once_the_shop_owner_says_yes_to_the_picture(
    call: ToolCall,
) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call)

    scene.owner_part_approved_at = timezone.now()

    assert owner_parts.is_approved(scene)


def test_a_picture_drawn_again_after_the_yes_needs_a_new_yes(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call, version=1)
    scene.owner_part_approved_at = timezone.now() - timedelta(minutes=1)

    a_picture(scene, call, version=2)

    assert not owner_parts.is_approved(scene)


def test_a_reference_photo_from_the_owner_parts_answer_has_no_fix(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)

    photo = ReferencePhoto.objects.create(
        scene=scene, file="owner/1.jpg", thing="the applicator", use=ReferencePhoto.Use.PICTURE
    )

    assert photo.added_by is None
    assert list(scene.photos_in_use()) == [photo]
