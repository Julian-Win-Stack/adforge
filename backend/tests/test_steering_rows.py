"""Whole-build check B (spec #247 Testing Decisions; ticket #265): the steering rows. The shop
owner steers an ad already shown in words, and the producer, faked and scripted per row
(the `producer` fixture), makes the tool calls the row needs. Each row checks what the
tools were told and answered, what the shop owner saw in the chat, what the fake video API
was sent, and the cost line. Every model is faked at the gateway; the clips are real tiny
videos."""

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from agents.money import dollars
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import Turn
from jobs import stale
from jobs.library import LookFrame, OwnerPhoto, library_of
from jobs.models import Job, ProducedItem, Scene

from .conftest import (
    LABELLED,
    NO_CHOICES,
    NOTHING_WATCHED,
    ScriptedProducer,
    chat,
    facts_ok,
    given_to_the_producer,
    handoffs,
    paid_for,
    photo,
    producer_turns,
    scripted_turns,
)
from .sent_lists import piece
from .test_cut_and_send import label, page_photo
from .test_make_picture import PASSES
from .test_producer_auto_fix import rewrite
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_fixes import shown  # noqa: F401 (a fixture)
from .test_producer_person_and_room import PLACES, ROOMS, five_shown  # noqa: F401
from .test_producer_plan_edits import AS_BROLL, LINE_1, LINE_3, SHORT_3, SHOWS_1, SHOWS_3

pytestmark = pytest.mark.django_db(transaction=True)


# --- What each row reads -----------------------------------------------------------------------


def calls_of(tool: str) -> list[ToolCall]:
    """Every call of `tool`, oldest first."""
    return list(ToolCall.objects.filter(tool=tool).order_by("id"))


def answer_of(tool: str) -> str:
    """What the newest call of `tool` handed back."""
    call = ToolCall.objects.filter(tool=tool).last()
    assert call is not None, f"{tool} was called"
    return call.result


def told(tool: str) -> list[dict[str, Any]]:
    """What each call of `tool` was told, oldest first, without its `why`."""
    return [call.arguments for call in calls_of(tool)]


def ads() -> list[ProducedItem]:
    return list(ProducedItem.objects.filter(kind="finished_ad").order_by("version"))


def spent_since(ad: ProducedItem, until: ProducedItem) -> Decimal:
    """What every model call cost after `ad` was assembled and before `until` was."""
    spent: Decimal = ModelCall.objects.filter(
        created_at__gt=ad.created_at, created_at__lt=until.created_at
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    return spent


def with_its_cost(what: str) -> Callable[[], Turn]:
    """The producer's one line to the shop owner after a change: `what` changed, and what it
    cost, as assemble_ad's answer told it."""

    def said() -> Turn:
        cost = re.search(r"cost (\$[0-9.]+):", answer_of("assemble_ad"))
        assert cost is not None, answer_of("assemble_ad")
        return turn(says=f"{what} It cost {cost.group(1)}.")

    return said


class Watched:
    """What a row is checked against: the chat, the fake video API and the money, as they
    stood when the shop owner started steering."""

    def __init__(self, fake_model: FakeModel, api: APIClient, session_id: str) -> None:
        self.fake_model = fake_model
        self._api = api
        self._session_id = session_id
        self.chat = len(chat(api, session_id))
        self.broll = len(fake_model.broll_clips_asked)
        self.talking = len(fake_model.clips_asked)
        self.paid = len(paid_for())
        self.ad = ads()[-1]

    def owner_saw(self) -> list[tuple[str, str]]:
        """Every message the shop owner saw since, theirs aside."""
        return [
            said for said in chat(self._api, self._session_id)[self.chat :] if said[0] != "user"
        ]

    def broll_sent(self) -> list[dict[str, Any]]:
        """What each B-roll clip asked of the fake video API since was sent."""
        return self.fake_model.broll_clips_asked[self.broll :]

    def talking_sent(self) -> list[tuple[float, bool, str]]:
        """Each talking clip asked of the fake video API since: seconds, audio, prompt."""
        return self.fake_model.clips_asked[self.talking :]

    def paid_for(self) -> list[str]:
        return paid_for()[self.paid :]

    def new_ad(self) -> ProducedItem:
        """The newest ad, assembled since, and shown to the shop owner."""
        ad = ads()[-1]
        assert ad.version == self.ad.version + 1
        assert Attachment.objects.filter(file=ad.file).exists()
        return ad

    def told_its_cost(self, what: str, ad: ProducedItem) -> None:
        """The shop owner saw the producer's one line on the change, with what it cost."""
        spent = dollars(spent_since(self.ad, ad))
        assert ("agent", f"{what} It cost {spent}.") in self.owner_saw()

    def cost_line(self, ad: ProducedItem) -> str:
        """The line assemble_ad hands the producer for the shop owner: what the changes since
        the ad before cost, every model call counted."""
        spent = dollars(spent_since(self.ad, ad))
        return f"The changes since ad version {self.ad.version} cost {spent}"


@pytest.fixture
def watched(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> Watched:
    """The three-scene ad (scenes 1 and 3 her talking, scene 2 the mug) shown, watched."""
    return Watched(fake_model, api, session_id)


def sent_of(clip: ProducedItem) -> dict[str, Any]:
    """The sent list kept on `clip`."""
    sent: dict[str, Any] | None = clip.sent
    assert sent is not None
    return sent


def sent_pictures(clip: ProducedItem) -> list[bytes]:
    """The pictures a clip's sent list names, as files."""
    return [file_store.read(picture["file"]) for picture in sent_of(clip)["pictures"]]


def newest_clip(scene: int) -> ProducedItem:
    clip = ProducedItem.objects.filter(kind="clip", scene__number=scene).order_by("version").last()
    assert clip is not None
    return clip


# --- "Make every scene B-roll" -----------------------------------------------------------------


def test_make_every_scene_broll(
    fake_model: FakeModel, producer: ScriptedProducer, watched: Watched
) -> None:
    fake_model.respond("fact_check", facts_ok(1, 3))
    producer.told(
        "Make every scene B-roll.",
        [
            ("edit_scene", {"scene": 1, "line": LINE_1, "shows": SHOWS_1, **AS_BROLL}),
            ("edit_scene", {"scene": 3, "line": LINE_3, "shows": SHOWS_3, **AS_BROLL}),
        ],
        ("run_planning_checks", NO_CHOICES),
        [("make_line_audio", {"scene": 1}), ("make_line_audio", {"scene": 3})],
    )
    # The edits were told the new scenes, and each answered what it made stale.
    assert [(asked["scene"], asked["shows"]) for asked in told("edit_scene")] == [
        (1, SHOWS_1),
        (3, SHOWS_3),
    ]
    first, third = [call.result for call in calls_of("edit_scene")]
    assert "Scene 1's audio, transcript and clip are stale" in first
    assert "Scene 3's audio, transcript and clip are stale" in third
    assert all(scene.shows for scene in Scene.objects.all())

    producer.woken(("transcribe_line_audio", {"scene": 1}))
    producer.woken(("transcribe_line_audio", {"scene": 3}))
    producer.woken(("make_clip", {"scene": 1}))
    producer.woken(("make_clip", {"scene": 3}))
    producer.woken()
    producer.woken(
        ("assemble_ad", {}), with_its_cost("Every scene is B-roll now; here's the new ad.")
    )

    # The video API was sent two B-roll clips, each from its sent list and no starting
    # picture, and no talking clip.
    sent = watched.broll_sent()
    assert len(sent) == 2
    for asked, scene in zip(sent, (1, 3), strict=True):
        clip = newest_clip(scene)
        assert asked["starting_picture"] is None
        assert asked["example_pictures"] == sent_pictures(clip)
        assert asked["prompt"] == sent_of(clip)["prompt"]
    assert [handoff["scene"]["shows"] for handoff in handoffs("write_first_send_prompt")][-2:] == [
        SHOWS_1,
        SHOWS_3,
    ]
    assert watched.talking_sent() == []
    assert stale.stale_steps(Scene.objects.get(number=2)) == []
    # The new ad keeps scene 2's clip; the producer is handed its cost line; the shop owner
    # saw the producer's line and the new ad.
    ad = watched.new_ad()
    assert ad.cuts[1]["clip"] == watched.ad.cuts[1]["clip"]
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("Every scene is B-roll now; here's the new ad.", ad)


# --- "Scene 2 is boring" -----------------------------------------------------------------------

LIVELIER = "Her hand swirls the tea in the mug, then lifts it up towards the light."


def test_a_boring_scene_gets_a_new_action_and_one_new_clip(
    fake_model: FakeModel, producer: ScriptedProducer, watched: Watched
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    producer.told(
        "Scene 2 is boring.",
        ("edit_scene", {"scene": 2, "shows": LIVELIER}),
        ("run_planning_checks", NO_CHOICES),
        ("make_clip", {"scene": 2}),
    )
    assert told("edit_scene") == [{"scene": 2, "shows": LIVELIER}]
    assert "Scene 2's clip is stale" in answer_of("edit_scene")
    producer.woken(("assemble_ad", {}), with_its_cost("Scene 2 has a livelier action now."))

    # One new clip, the scene's alone, sent its new action through the first-send writer.
    (asked,) = watched.broll_sent()
    clip = newest_clip(2)
    assert asked["example_pictures"] == sent_pictures(clip)
    assert asked["prompt"] == sent_of(clip)["prompt"]
    assert handoffs("write_first_send_prompt")[-1]["scene"]["shows"] == LIVELIER
    assert watched.talking_sent() == []
    assert watched.paid_for().count("make_broll_clip") == 1
    ad = watched.new_ad()
    assert [cut["clip"] for cut in ad.cuts] == [
        watched.ad.cuts[0]["clip"],
        clip.pk,
        watched.ad.cuts[2]["clip"],
    ]
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("Scene 2 has a livelier action now.", ad)


# --- "Make it shorter" -------------------------------------------------------------------------


def test_make_it_shorter_makes_audio_and_a_clip_only_where_the_line_changed(
    fake_model: FakeModel, producer: ScriptedProducer, watched: Watched
) -> None:
    fake_model.respond("fact_check", facts_ok(3))
    producer.told(
        "Make it shorter.",
        ("edit_scene", {"scene": 3, "line": SHORT_3}),
        ("run_planning_checks", NO_CHOICES),
        ("make_line_audio", {"scene": 3}),
    )
    assert told("edit_scene") == [{"scene": 3, "line": SHORT_3}]
    assert "Scene 3's audio, transcript and clip are stale" in answer_of("edit_scene")
    producer.woken(("transcribe_line_audio", {"scene": 3}))
    producer.woken(("make_clip", {"scene": 3}))
    producer.woken(("assemble_ad", {}), with_its_cost("The last line is shorter now."))

    # Only scene 3's voice and clip were made again: one talking clip, from its new audio.
    assert watched.paid_for() == [
        "fact_check",
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]
    ((seconds, with_audio, _),) = watched.talking_sent()
    assert with_audio
    assert watched.broll_sent() == []
    ad = watched.new_ad()
    assert ad.cuts[:2] == watched.ad.cuts[:2]
    assert ad.cuts[2]["clip"] == newest_clip(3).pk
    assert ad.seconds is not None and watched.ad.seconds is not None
    assert ad.seconds < watched.ad.seconds
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("The last line is shorter now.", ad)


# --- "Slower" ----------------------------------------------------------------------------------

REAL_TIME = (
    "Clips play at real-time speed, so I can't slow them down. I can make the action calmer "
    "instead: shall I?"
)


def test_slower_is_answered_with_real_time_speed_and_nothing_is_made(
    producer: ScriptedProducer, watched: Watched
) -> None:
    calls = ToolCall.objects.count()

    producer.told("Make scene 2 slower.", REAL_TIME)

    assert watched.owner_saw() == [("agent", REAL_TIME)]
    assert ToolCall.objects.count() == calls
    assert watched.paid_for() == []
    assert (watched.broll_sent(), watched.talking_sent()) == ([], [])
    assert producer.steps.held == []
    assert ads()[-1] == watched.ad


# --- "Now do my other product" -----------------------------------------------------------------

ANOTHER_AD = "That's another product: start a new ad for that, with the New session button."


def test_another_product_is_answered_start_a_new_ad_for_that(
    producer: ScriptedProducer, watched: Watched, page_read: str, httpserver: HTTPServer
) -> None:
    other = httpserver.url_for("/products/teapot")
    httpserver.expect_request("/products/teapot").respond_with_data(
        f'<html><head><link rel="canonical" href="{other}"></head>'
        "<body><h1>Glass Teapot</h1></body></html>",
        content_type="text/html",
    )

    producer.told(
        f"Now do my other product: {other}",
        ("read_page", {"link": other, "target_seconds": None}),
        ANOTHER_AD,
    )

    assert told("read_page")[-1] == {"link": other, "target_seconds": None}
    call = calls_of("read_page")[-1]
    assert call.answer == "cant"
    assert "start a new ad for that" in call.result
    assert watched.owner_saw() == [("agent", ANOTHER_AD)]
    assert Job.objects.get().product_url == page_read
    assert watched.paid_for() == []
    assert (watched.broll_sent(), watched.talking_sent()) == ([], [])
    assert ads()[-1] == watched.ad


# --- "Use the photo with the lid off for scene 2" ----------------------------------------------

GREEN = "sage-green"
LID_OFF = label("state", "front", GREEN, "The mug with its lid off, from the front.")
BACK = label("side", "back", GREEN, "The mug from the back.")
SIDE = label("side", "side", GREEN, "The mug from its side.")
IN_USE = label("in-use", "none", GREEN, "A hand holding the mug by its handle.", alone="no")


def test_the_photo_with_the_lid_off_is_sent_for_scene_2_and_an_excluded_side_never(
    producer: ScriptedProducer, watched: Watched
) -> None:
    # The page also had the mug's back, its side, its lid off and six photos of it in use.
    job = Job.objects.get()
    back, side, lid_off = (page_photo(job, picture) for picture in (BACK, SIDE, LID_OFF))
    in_use = [page_photo(job, IN_USE) for _ in range(6)]
    references = {
        "scene": 2,
        "pictures": [
            {"number": lid_off, "for": "state"},
            *({"number": number, "for": "in-use"} for number in in_use),
        ],
        "piece": None,
        "exclude": [back],
    }

    producer.told(
        "Use the photo with the lid off for scene 2, and never its back.",
        ("set_references", references),
        ("make_clip", {"scene": 2}),
    )
    assert told("set_references") == [references]
    answer = answer_of("set_references")
    assert answer.startswith(f"Scene 2's references: pictures {lid_off} (state), ")
    assert f'Not sent, as excluded for scene 2: "{BACK.label}" (library item {back}' in answer
    producer.woken(
        ("assemble_ad", {}), with_its_cost("Scene 2 shows the mug with its lid off now.")
    )

    # Sent Image 1, the lid off in tier 2 first, its room and as many as the cap takes; the
    # back never, and the cut named in the clip's answer to the producer.
    clip = newest_clip(2)
    assert clip.sent is not None
    pictures = clip.sent["pictures"]
    assert len(pictures) == 9
    assert [(p["tier"], p["library_number"]) for p in pictures[:2]] == [(1, 1), (2, lid_off)]
    assert [p["tier"] for p in pictures[2:]] == [2] * 6 + [3]
    assert back not in [p["library_number"] for p in pictures]
    # The side is the one the cap cut.
    assert [p["library_number"] for p in clip.sent["left_out"]] == [side]
    step = clip.step
    assert step is not None
    assert "Not sent, as excluded for scene 2" in step.result
    assert "left out" in step.result
    (asked,) = watched.broll_sent()
    assert asked["example_pictures"] == sent_pictures(clip)
    assert (
        file_store.read(Job.objects.get().library.get(number=back).file)
        not in asked["example_pictures"]
    )
    # The shop owner saw the clip, the cut in red, the producer's line and the new ad.
    saw = watched.owner_saw()
    assert any(role == "notice" and "left out" in text for role, text in saw), saw
    ad = watched.new_ad()
    assert ad.cuts[1]["clip"] == clip.pk
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("Scene 2 shows the mug with its lid off now.", ad)


# --- make_picture, then set_references ---------------------------------------------------------

MADE_PROMPT = "The mug from picture 1, full of steaming tea, on a plain light background."
MADE_LABEL = "The mug full of steaming tea, from the front."


def test_a_made_picture_is_stored_named_by_set_references_and_sent(
    fake_model: FakeModel, producer: ScriptedProducer, watched: Watched
) -> None:
    fake_model.respond("check_made_picture", PASSES)
    # Labelled in the ad's colour, so it goes as it was made, not recoloured.
    fake_model.respond("label_photo", {**LABELLED, "colour": GREEN})
    producer.told(
        "Show the mug full of tea in scene 2.",
        ("make_picture", {"prompt": MADE_PROMPT, "references": [1, 2], "label": MADE_LABEL}),
        (
            "set_references",
            {
                "scene": 2,
                "pictures": [{"number": 3, "for": "result"}],
                "piece": None,
                "exclude": None,
            },
        ),
        ("make_clip", {"scene": 2}),
    )
    # The picture tool was told the prompt and the references, and stored item 3 with the
    # producer's label, drawn from items 1 and 2.
    made = Job.objects.get().library.get(number=3)
    assert answer_of("make_picture").startswith("Made library item 3")
    assert made.label == MADE_LABEL
    assert handoffs("make_picture")[-1]["prompt"] == MADE_PROMPT
    assert answer_of("set_references").startswith("Scene 2's references: pictures 3 (result)")
    producer.woken(("assemble_ad", {}), with_its_cost("Scene 2 ends on the mug full of tea now."))

    # The new clip was sent it in tier 2, after Image 1.
    clip = newest_clip(2)
    assert clip.sent is not None
    assert [(p["tier"], p["library_number"]) for p in clip.sent["pictures"][:2]] == [
        (1, 1),
        (2, 3),
    ]
    (asked,) = watched.broll_sent()
    assert asked["example_pictures"] == sent_pictures(clip)
    assert file_store.read(made.file) in asked["example_pictures"]
    assert watched.paid_for()[:3] == ["make_picture", "check_made_picture", "label_photo"]
    ad = watched.new_ad()
    assert ad.cuts[1]["clip"] == clip.pk
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("Scene 2 ends on the mug full of tea now.", ad)


# --- "Go back to the first version of scene 2" -------------------------------------------------


MORE_STEAM = "Let more steam rise from the tea."


def test_going_back_to_the_first_version_of_scene_2_reassembles_and_pays_nothing(
    fake_model: FakeModel, producer: ScriptedProducer, watched: Watched
) -> None:
    first = newest_clip(2)
    fake_model.respond("rewrite_clip_prompt", rewrite(MORE_STEAM))
    producer.told(
        "Make scene 2 again with more steam.",
        ("make_clip", {"scene": 2, "note": MORE_STEAM}),
    )
    producer.woken(("assemble_ad", {}), "Scene 2 has more steam now.")
    assert newest_clip(2).pk != first.pk
    paid, broll, talking = (
        len(paid_for()),
        len(fake_model.broll_clips_asked),
        len(fake_model.clips_asked),
    )
    second_ad = ads()[-1]

    producer.told(
        "Go back to the first version of scene 2.",
        ("use_version", {"scene": 2, "version": 1}),
        "Scene 2 is back to its first version; it cost nothing.",
    )

    assert told("use_version") == [{"scene": 2, "version": 1}]
    answer = answer_of("use_version")
    assert answer.startswith("Scene 2 uses v1")
    assert "nothing was made or paid for" in answer
    assert answer.endswith("it cost nothing.")
    # Nothing made, sent or paid for; the ad assembled again with the first clip.
    assert len(paid_for()) == paid
    assert (len(fake_model.broll_clips_asked), len(fake_model.clips_asked)) == (broll, talking)
    assert producer.steps.held == []
    ad = ads()[-1]
    assert ad.version == second_ad.version + 1
    assert Attachment.objects.filter(file=ad.file).exists()
    assert ProducedItem.objects.get(pk=ad.cuts[1]["clip"]).file == first.file
    assert ("agent", "Scene 2 is back to its first version; it cost nothing.") in (
        watched.owner_saw()
    )


# --- "Show her in five different rooms" --------------------------------------------------------


def test_five_different_rooms(
    fake_model: FakeModel,
    five_shown: ProducedItem,  # noqa: F811
    producer: ScriptedProducer,
    api: APIClient,
    session_id: str,
) -> None:
    watching = Watched(fake_model, api, session_id)
    producer.told(
        "Show her in five different rooms.",
        [("set_place", {"scene": n, "new_place": PLACES[n]}) for n in ROOMS],
        [("make_clip", {"scene": n}) for n in ROOMS],
    )
    assert [(asked["scene"], asked["new_place"]) for asked in told("set_place")] == [
        (n, PLACES[n]) for n in ROOMS
    ]
    for call in calls_of("set_place"):
        assert "clip is stale now" in call.result
    for _ in ROOMS[:-1]:
        producer.woken()
    producer.woken(("assemble_ad", {}), with_its_cost("She's in five different rooms now."))

    # Five rooms and five pictures of her in them, each shown; five B-roll clips, each
    # sent the presenter in its own scene's room.
    assert watching.paid_for()[:10] == ["draw_scene_room", "make_scene_person_in_room"] * 5
    sent = watching.broll_sent()
    assert len(sent) == 5
    for asked, number in zip(sent, ROOMS, strict=True):
        scene = Scene.objects.get(number=number)
        person = scene.produced.get(kind="scene_person_in_room")
        assert Attachment.objects.filter(file=person.file).exists()
        clip = newest_clip(number)
        assert asked["example_pictures"] == sent_pictures(clip)
        assert file_store.read(person.file) in asked["example_pictures"]
    assert watching.talking_sent() == []
    ad = watching.new_ad()
    assert ad.cuts[0] == five_shown.cuts[0]
    assert watching.cost_line(ad) in answer_of("assemble_ad")
    watching.told_its_cost("She's in five different rooms now.", ad)


# --- An owner attachment: a photo and a small video ---------------------------------------------

LID_ON = "the mug with its lid on"
LID_MOMENT = {"start": 0.0, "end": 2.5, "happens": "The lid is pressed on.", "on_screen": "A mug."}


def test_an_owner_photo_and_video_enter_the_library_and_are_sent_for_scene_2(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,
    api: APIClient,
    session_id: str,
) -> None:
    job = Job.objects.get()
    photo_number, frame_number = len(library_of(job)) + 1, len(library_of(job)) + 2
    fake_model.respond(
        "watch_video",
        {
            **NOTHING_WATCHED,
            "colour": GREEN,
            "moments": [LID_MOMENT],
            "look_frames": [{**LABELLED, "kind": "state", "colour": GREEN, "second": 1.0}],
        },
    )
    fake_model.respond("label_photo", {**LABELLED, "colour": GREEN, "label": "The mug, lid on."})
    fake_model.respond(
        "produce",
        *scripted_turns(
            ("add_to_library", {"photos": [{"photo": 1, "shows": LID_ON}], "videos": [1]}),
            (
                "set_references",
                {
                    "scene": 2,
                    "pictures": [{"number": photo_number, "for": "state"}],
                    "piece": {"video": 1, "moment": 1},
                    "exclude": None,
                },
            ),
            ("make_clip", {"scene": 2}),
        ),
    )
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": "Here's the mug with its lid on, and how the lid goes on. Use them in scene 2.",
            "photos": [SimpleUploadedFile("lid.png", photo(7), content_type="image/png")],
            "videos": [SimpleUploadedFile("lid.mp4", piece(3.0), content_type="video/mp4")],
        },
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()
    assert fake_model.left("produce") == 0
    # The producer read them numbered, as the tool takes them.
    assert "[Attached 1 picture and 1 video: owner photo 1 and owner video 1]" in str(
        given_to_the_producer(producer_turns() - 3)
    )

    # The photo is in the library labelled, the video kept, watched and its look frame added.
    assert told("add_to_library") == [{"photos": [{"photo": 1, "shows": LID_ON}], "videos": [1]}]
    entries = {item.number: item for item in library_of(job)}
    assert entries[photo_number].source == OwnerPhoto(original=entries[photo_number].file)
    assert handoffs("label_photo")[-1]["shows"] == LID_ON
    (video,) = job.videos.all()
    assert entries[frame_number].source == LookFrame(video=video.position, second=1.0)
    answer = answer_of("add_to_library")
    assert f"Their photo is library item {photo_number}" in answer
    assert f"Their video is page video {video.position}, watched: moments 1 to 1" in answer
    assert answer_of("set_references").startswith(
        f"Scene 2's references: pictures {photo_number} (state); video piece video 1, moment 1"
    )
    producer.woken(("assemble_ad", {}), with_its_cost("Scene 2 uses your photo and video now."))

    # The clip was sent the photo in tier 2 and the piece from the video's moment.
    clip = newest_clip(2)
    (asked,) = watched.broll_sent()
    assert asked["example_pictures"] == sent_pictures(clip)
    assert file_store.read(entries[photo_number].file) in asked["example_pictures"]
    ((piece_sent),) = asked["reference_videos"]
    assert (piece_sent["start"], piece_sent["end"]) == (0.0, 2.5)
    # The shop owner saw where their files went, the producer's line and the new ad.
    saw = watched.owner_saw()
    assert (
        "notice",
        f"What you sent is in the ad's picture library as items {photo_number} and {frame_number}.",
    ) in saw
    ad = watched.new_ad()
    assert ad.cuts[1]["clip"] == clip.pk
    assert watched.cost_line(ad) in answer_of("assemble_ad")
    watched.told_its_cost("Scene 2 uses your photo and video now.", ad)


def test_an_attachment_named_wrongly_is_refused_and_nothing_is_added(
    producer: ScriptedProducer, watched: Watched
) -> None:
    before = len(library_of(Job.objects.get()))

    producer.told(
        "Use my photo.",
        ("add_to_library", {"photos": [{"photo": 1, "shows": LID_ON}], "videos": []}),
        "I don't see a photo: could you attach it?",
    )

    assert answer_of("add_to_library") == (
        "Refused: the chat has no owner photo and no owner video. Nothing was done."
    )
    assert len(library_of(Job.objects.get())) == before
    assert watched.paid_for() == []
