"""The owner part on the scene record (owner-part approval, T0): a part of the product no
photo shows, drawn from the shop owner's words or invented by the planner, which the shop
owner approves in the starting picture before the scene's clip is made. The scene keeps it
and when it was approved; each step snapshots it, as it does the scene's needs. Run on the
real database: no model is called."""

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
        **{
            "job": call.job,
            "number": number,
            "line": "One dab and it lasts all day.",
            "shows": "A hand dabs the tint on a cheek.",
            "broll_kind": Scene.BrollKind.DOES_A_JOB,
            "person_shown": Scene.PersonShown.NO_FACE,
            "usage": "Dab it on, blend it in.",
            **fields,
        }
    )


def a_picture(scene: Scene, call: ToolCall, version: int = 1) -> ProducedItem:
    """A starting picture drawn for `scene` as it stands now, with the photos it uses."""
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.STARTING_PICTURE,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        **SceneStep.snapshot(scene),
    )
    step.reference_photos.set(scene.photos_in_use())
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


def test_a_picture_drawn_again_for_the_part_as_approved_keeps_the_yes(call: ToolCall) -> None:
    # The yes is for the part as the shop owner saw it: a later picture of it, such as a
    # fix's, isn't asked about again (T4).
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call, version=1)
    scene.owner_part_approved_at = timezone.now()

    a_picture(scene, call, version=2)

    assert owner_parts.is_approved(scene)


def test_a_picture_drawn_after_the_yes_for_a_changed_part_needs_a_new_yes(
    call: ToolCall,
) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call, version=1)
    scene.owner_part_approved_at = timezone.now()

    scene.owner_part = {**APPLICATOR, "looks": "A pink foam tip on a short gold stem."}
    scene.save()
    a_picture(scene, call, version=2)

    assert not owner_parts.is_approved(scene)


def test_a_picture_drawn_after_the_yes_with_a_new_photo_of_the_part_needs_a_new_yes(
    call: ToolCall,
) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)
    a_picture(scene, call, version=1)
    scene.owner_part_approved_at = timezone.now()

    a_photo_of_the_part(scene)
    a_picture(scene, call, version=2)

    assert not owner_parts.is_approved(scene)


def test_a_reference_photo_from_the_owner_parts_answer_has_no_fix(call: ToolCall) -> None:
    scene = a_broll_scene(call, owner_part=APPLICATOR)

    photo = ReferencePhoto.objects.create(
        scene=scene, file="owner/1.jpg", thing="the applicator", use=ReferencePhoto.Use.PICTURE
    )

    assert photo.added_by is None
    assert list(scene.photos_in_use()) == [photo]


def a_photo_of_the_part(scene: Scene, file: str = "owner/1.jpg") -> ReferencePhoto:
    return ReferencePhoto.objects.create(
        scene=scene,
        file=file,
        thing="applicator",
        use=ReferencePhoto.Use.PICTURE,
        drawn=f"clean/{file}",
    )


def scene_numbers_with(part: dict[str, str]) -> list[int]:
    return list(
        Scene.objects.filter(owner_part=part, owner_part_approved_at__isnull=False)
        .order_by("number")
        .values_list("number", flat=True)
    )


def test_the_yes_keeps_the_part_and_its_photos_for_every_scene_that_shows_it(
    call: ToolCall,
) -> None:
    approved = a_broll_scene(call, 2, owner_part=APPLICATOR)
    # The planner gave scene 3 the same part, in its own words; scene 4's plan uses it with
    # no owner part of its own; scene 5 shows a different part, and scene 6 doesn't show it.
    a_broll_scene(
        call, 3, owner_part={**APPLICATOR, "what": "The  Applicator", "looks": "A white tip."}
    )
    a_broll_scene(call, 4, usage="Sweep the applicator along the cheekbone.")
    lid = {"what": "the inner lid", "source": "invented", "looks": "A clear plastic disc."}
    a_broll_scene(call, 5, owner_part=lid, usage="Lift the inner lid and dab.")
    a_broll_scene(call, 6, shows="The tint's box on a shelf.", usage="")
    photo = a_photo_of_the_part(approved)
    a_picture(approved, call)

    owner_parts.approve(approved)

    assert scene_numbers_with(APPLICATOR) == [2, 3, 4]
    assert Scene.objects.get(number=5).owner_part == lid
    assert Scene.objects.get(number=5).owner_part_approved_at is None
    copied = ReferencePhoto.objects.exclude(pk=photo.pk)
    assert sorted(copied.values_list("scene__number", "file", "thing", "use", "drawn")) == [
        (number, "owner/1.jpg", "applicator", "picture", "clean/owner/1.jpg") for number in (3, 4)
    ]
    assert not copied.filter(added_by__isnull=False).exists()
    # Every scene showing it is approved, its own picture still to be drawn with the part.
    for number in (2, 3, 4):
        assert owner_parts.is_approved(Scene.objects.get(number=number))


def test_a_photo_already_kept_isnt_copied_again_and_an_older_one_is_set_aside(
    call: ToolCall,
) -> None:
    approved = a_broll_scene(call, 2, owner_part=APPLICATOR)
    other = a_broll_scene(call, 3, owner_part=APPLICATOR)
    a_photo_of_the_part(approved, "owner/2.jpg")
    kept = a_photo_of_the_part(other, "owner/2.jpg")
    older = a_photo_of_the_part(other, "owner/1.jpg")
    a_picture(approved, call)

    owner_parts.approve(approved)

    assert list(Scene.objects.get(number=3).photos_in_use()) == [kept]
    older.refresh_from_db()
    assert older.set_aside_at is not None
    assert scene_numbers_with(APPLICATOR) == [2, 3]


def test_a_scene_whose_photo_shows_the_part_isnt_given_it(call: ToolCall) -> None:
    approved = a_broll_scene(call, 2, owner_part=APPLICATOR)
    a_broll_scene(
        call,
        3,
        usage="Sweep the applicator along the cheekbone.",
        needs=[{"what": "the applicator", "photos": [2]}],
    )
    a_picture(approved, call)

    owner_parts.approve(approved)

    assert Scene.objects.get(number=3).owner_part is None


def test_a_word_that_only_contains_the_parts_name_isnt_it(call: ToolCall) -> None:
    tip = {"what": "the tip", "source": "invented", "looks": "A rounded white tip."}
    approved = a_broll_scene(call, 2, owner_part=tip)
    a_broll_scene(call, 3, usage="Multiple layers build the colour.")
    a_picture(approved, call)

    owner_parts.approve(approved)

    assert Scene.objects.get(number=3).owner_part is None
