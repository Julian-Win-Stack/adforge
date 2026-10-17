"""The decider's replay and rule cases (evals.decider, fix redesign ticket 07): a case is
played on a finished ad through the real decider, loop and database, with the model faked
here, and everything it did is kept as files, checked against the case's expected answers,
and shown on Julian's grading page."""

import io
import json
from pathlib import Path
from typing import Any

import PIL.Image
import pytest

from adforge import file_store
from agents import decider
from agents.models import ToolCall
from chat import messages
from chat.models import Message, Session
from evals.decider import page, score
from evals.decider.__main__ import _write_results, forget_the_faked_judge, report, run
from evals.decider.play import play
from evals.decider.record import record
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job, ProducedItem, Scene, SceneStep

pytestmark = pytest.mark.django_db(transaction=True)


def _png(colour: tuple[int, int, int]) -> bytes:
    made = io.BytesIO()
    PIL.Image.new("RGB", (30, 40), colour).save(made, format="PNG")
    return made.getvalue()


@pytest.fixture
def ad() -> Job:
    """A finished ad as the owner-notes run leaves it: scene 2 shows the product scrubbing a
    toilet, its one clip "passed" by the faked judge, with its frames kept."""
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/products/gel")
    call = ToolCall.objects.create(
        session=session, job=job, agent="producer", tool="make_clip", call_id="c0", arguments={}
    )
    # The person's voice, measured, as every ad made through the chat has it.
    ProducedItem.objects.create(
        job=job, kind=ProducedItem.Kind.VOICE, version=1, voice_id="v1", words_per_second=2.5
    )
    scene = Scene.objects.create(
        job=job,
        number=2,
        line="One wipe and it shines.",
        shows="A hand scrubs a toilet bowl with the gel.",
        broll_kind=Scene.BrollKind.DOES_A_JOB,
        person_shown=Scene.PersonShown.NO_FACE,
        usage="Squeeze the gel on, scrub, rinse.",
    )
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.STARTING_PICTURE,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        line=scene.line,
        shows=scene.shows,
    )
    picture = ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=1,
        file=file_store.save("s2.png", _png((200, 200, 200))),
    )
    frames = [
        {"times": [0.0], "key": file_store.save("f0.png", _png((1, 2, 3)))},
        {"times": [0.2], "key": file_store.save("f1.png", _png((4, 5, 6)))},
    ]
    ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=SceneStep.objects.create(
            scene=scene,
            kind=SceneStep.Kind.CLIP,
            status=SceneStep.Status.FINISHED,
            tool_call=call,
            line=scene.line,
            shows=scene.shows,
            picture=picture,
            picture_step=step,
            motion_prompt="A hand scrubs the bowl.",
        ),
        kind=ProducedItem.Kind.CLIP,
        version=1,
        file="s2.mp4",
        picture=picture,
        # The line's audio it speaks, as code estimates a fix of it from.
        made_from=ProducedItem.objects.create(
            job=job,
            scene=scene,
            kind=ProducedItem.Kind.LINE_AUDIO,
            version=1,
            file="s2.mp3",
            seconds=4.0,
        ),
        ruling={"verdict": "pass", "checks": {}, "pictures": {"frames": frames, "sides": []}},
    )
    return job


def _row(scene: int, action: str, change: str = "", **fields: Any) -> dict[str, Any]:
    return {
        "scene": scene,
        "action": action,
        "change": change,
        "line": "",
        "go_back_to": None,
        "cant": "",
        "photos": [],
        **fields,
    }


def _decided(scenes: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    return {"scenes": scenes, "photos": [], "cants": [], "question": "", **fields}


def test_a_case_is_played_and_everything_it_did_is_kept(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    owner_photo = tmp_path / "black-toilet.png"
    owner_photo.write_bytes(_png((0, 0, 0)))
    case = {
        "id": "rule-photo-in-picture",
        "layer": "rule",
        "rule": "route",
        "side": "pass",
        "ad": "8",
        "turns": [
            {"say": "Use our black toilet in scene 2.", "photos": [str(owner_photo)]},
            {"say": "yeah go"},
        ],
        "expect": [
            {
                "outcome": "planned",
                "scenes": {"2": "new_picture"},
                "thing_has": {"1": "white"},
                "looks": [["look_at_picture", 2]],
                "judge_findings": False,
            },
            {"outcome": "yes"},
        ],
    }
    uses = [{"photo": 1, "in_picture": True, "during_action": False}]
    plan = _decided(
        [_row(2, "new_picture", "The toilet is the shop owner's black toilet.", photos=uses)],
        photos=[{"photo": 1, "thing": "black toilet", "clear": True}],
    )
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("decide", plan)]),
        turn(calls=[("look_at_picture", {"scene": 2}), ("look_at_owner_photo", {"photo": 1})]),
        turn(calls=[("decide", plan)]),
        turn(calls=[("sort_message", {"kind": "yes"})]),
    )
    out = tmp_path / "run"
    assert ad.session is not None
    messages.add(
        ad.session,
        role=Message.Role.NOTICE,
        level=Message.Level.PROBLEM,
        text="Making the first version went wrong once.",
    )

    result = record(case, play(ad, case), out, model="gpt-6.1-sol")

    folder = out / "rule-photo-in-picture"
    # Only what the case did is its own: the ad's red notice from before it isn't.
    assert result["red_notices"] == []
    assert [each["outcome"] for each in result["turns"]] == ["planned", "yes"]
    first = result["turns"][0]
    assert first["decide"]["photos"] == [{"photo": 1, "thing": "black toilet", "clear": True}]
    assert [row["action"] for row in first["plan"]["scenes"]] == ["new_picture"]
    # The decide made before any look was refused by code, and that is kept.
    assert [each["tool"] for each in result["refused"]] == ["decide"]
    assert result["refused"][0]["result"].startswith("Refused: look at scene 2 first")
    # Every picture it was shown is kept, named by its label, and its tool call points at it.
    calls = json.loads((folder / "tool_calls.json").read_text())
    shown = [picture for call in calls for picture in call["pictures"]]
    assert [picture["label"] for picture in shown] == [
        "Scene 2, starting picture, v1 first version (the current version)",
        "Owner photo 1",
    ]
    assert (folder / shown[0]["file"]).read_bytes() == _png((200, 200, 200))
    assert (folder / shown[1]["file"]).read_bytes() == _png((0, 0, 0))
    # Each model call is kept with what it was given and what it answered, and what it
    # was first given for each message is kept on its own.
    model_calls = json.loads((folder / "model_calls.json").read_text())
    assert len(model_calls) == 5
    assert model_calls[3]["pictures_shown"] == [label["label"] for label in shown]
    assert model_calls[3]["output"]["calls"][0]["tool"] == "decide"
    given = json.loads((folder / "given.json").read_text())
    assert [each["messages"][-1] for each in given] == [
        "Use our black toilet in scene 2.\n\n[Attached 1 picture: owner photo 1]",
        "yeah go",
    ]
    # The photo the shop owner sent is kept with the message, looked at or not.
    assert [each["photos"] for each in given] == [["pictures/sent-black-toilet.png"], []]
    assert (folder / "pictures" / "sent-black-toilet.png").read_bytes() == _png((0, 0, 0))
    # The scene it decided was looked at first; the decide refused before the look is no row.
    assert result["rows"] == [{"scene": 2, "looked": True}]
    # What the decider was told is kept once, and the result names it.
    told = json.loads((out / "told" / f"{result['told']}.json").read_text())
    assert told["instructions"] == decider.INSTRUCTIONS
    assert "decide" in [tool["name"] for tool in told["tools"]]
    assert "Scene 2: shows the product" in given[0]["the_ad"]
    assert json.loads((folder / "case.json").read_text()) == case
    assert json.loads((folder / "result.json").read_text()) == result
    # Its answers are checked against the expected ones: the thing named here differs.
    checks = {(check["turn"], check["what"]): check["same"] for check in result["checks"]}
    assert checks == {
        (1, "outcome"): True,
        (1, "scenes"): True,
        (1, "thing_has"): False,
        (1, "looks"): True,
        (1, "judge_findings"): True,
        (2, "outcome"): True,
    }


def test_an_earlier_version_from_a_past_run_can_be_gone_back_to(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    drawn = tmp_path / "scene-2-picture-2.png"
    drawn.write_bytes(_png((0, 0, 0)))
    case: dict[str, Any] = {
        "id": "rule-go-back-first",
        "ad": "8",
        "setup": {"versions": [{"scene": 2, "picture": str(drawn), "change": "black toilet"}]},
        "turns": [{"say": "Scene 2: I liked the first one better."}],
        "expect": [{"scenes": {"2": "go_back:1"}}],
    }
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_version", {"scene": 2, "version": 1})]),
        turn(calls=[("decide", _decided([_row(2, "go_back", go_back_to=1)]))]),
    )

    result = record(case, play(ad, case), tmp_path / "run", model="gpt-6.1-sol")

    the_ad = json.loads((tmp_path / "run" / case["id"] / "given.json").read_text())[0]["the_ad"]
    assert "Versions: v1 first version; v2 black toilet (the current version)" in the_ad
    assert result["checks"][0]["same"]


def test_a_decider_that_stops_without_an_answer_is_kept_as_such(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    case = {"id": "quiet", "ad": "8", "turns": [{"say": "Scene 2 is too dark."}], "expect": []}
    fake_model.respond("decide_fixes", turn("hmm"), turn("hmm"), turn("hmm"))

    result = record(case, play(ad, case), tmp_path / "run", model="gpt-6.1-sol")

    [stopped] = result["turns"]
    assert stopped["outcome"] == "no_decision"
    assert "reminded" in stopped["why"]


def test_a_case_a_model_refuses_is_kept_with_what_it_did_and_paid(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    case = {
        "id": "refused",
        "ad": "8",
        "turns": [{"say": "Scene 2 is too dark."}, {"say": "yes"}],
        "expect": [{"outcome": "planned"}, {"outcome": "yes"}],
    }
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        BlockedBySafetyFilter("the model wouldn't answer"),
    )

    result = record(case, play(ad, case), tmp_path / "run", model="gpt-6.1-sol")

    # The case stops at the error, and the call it made before it is kept.
    [stopped] = result["turns"]
    assert stopped["outcome"] == "error"
    assert "BlockedBySafetyFilter" in stopped["why"]
    assert [
        call["tool"]
        for call in json.loads((tmp_path / "run" / "refused" / "tool_calls.json").read_text())
    ] == ["sort_message"]
    assert result["checks"][0]["same"] is False


def test_the_judge_the_replay_faked_is_not_told_to_the_decider(ad: Job) -> None:
    forget_the_faked_judge(ad)

    ruling = ad.produced.get(kind=ProducedItem.Kind.CLIP).ruling
    assert ruling is not None
    assert "verdict" not in ruling
    assert len(ruling["pictures"]["frames"]) == 2


def test_the_run_is_counted_reported_and_shown_on_a_grading_page(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    case = {
        "id": "rule-not-a-fix-cost",
        "layer": "rule",
        "rule": "fix note or not",
        "side": "fail",
        "ad": "8",
        "turns": [{"say": "What does a fix cost?"}],
        "expect": [{"outcome": "not_a_fix"}],
        "past": {"producer": "answered the cost", "grade": "pass"},
    }
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    out = tmp_path / "run"
    played = record(case, play(ad, case), out, model="gpt-6.1-sol")

    _write_results(out, [played], "gpt-6.1-sol", failed=["rule-broken"])
    made = page.write(out)

    assert score.counts([played]) == {"kind": [1, 1]}
    text = made.read_text()
    assert "rule-not-a-fix-cost" in text
    assert "Not a fix note: handed to the producer" in text
    assert "answered the cost" in text
    assert 'data-doc="rule-not-a-fix-cost"' in text
    # It says how many of the judge's findings had a judged clip behind them.
    assert "0 of its 0 calls found a clip a judge had judged" in text
    assert "Scenes decided after a look at that scene: 0 of 0." in text
    printed = report(out)
    assert "Red notices" in printed
    assert "Cases that didn't run: rule-broken" in printed
    assert "OpenAI spend: $" in printed


def test_a_rerun_into_the_same_folder_replaces_what_it_reran(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    case = {"id": "rule-sort-thanks", "ad": "8", "turns": [{"say": "Thanks!"}], "expect": []}
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    out = tmp_path / "run"
    played = record(case, play(ad, case), out, model="gpt-6.1-sol")
    _write_results(out, [played], "gpt-6.1-sol", failed=[])

    # It fails on the rerun: its old answer isn't shown as this run's.
    _write_results(out, [], "gpt-6.1-sol", failed=["rule-sort-thanks"])
    kept = json.loads((out / "results.json").read_text())
    assert (kept["cases"], kept["failed"]) == ([], ["rule-sort-thanks"])
    assert "Cases that didn't run: rule-sort-thanks" in page.page(out)

    # A later rerun of another case leaves it failed; running it again clears it.
    _write_results(out, [{**played, "id": "other"}], "gpt-6.1-sol", failed=[])
    assert json.loads((out / "results.json").read_text())["failed"] == ["rule-sort-thanks"]
    _write_results(out, [played], "gpt-6.1-sol", failed=[])
    assert json.loads((out / "results.json").read_text())["failed"] == []

    # A run on another model gets a folder of its own.
    with pytest.raises(SystemExit, match="another model"):
        run([], out, "gpt-5.6-sol", parallel=1)


def test_a_picture_looked_at_twice_is_shown_once_on_the_page(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    case = {"id": "twice", "ad": "8", "turns": [{"say": "Scene 2 is too dark."}], "expect": []}
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", _decided([_row(2, "new_picture", "Brighter.")]))]),
    )
    out = tmp_path / "run"
    _write_results(out, [record(case, play(ad, case), out, model="gpt-6.1-sol")], "m", [])

    text = page.page(out)

    assert text.count("<img") == 1
    assert "The same 1 pictures as above." in text


def test_expected_answers_that_leave_room_are_checked_as_such() -> None:
    decided = _decided(
        [_row(2, "new_picture", "Black.", cant="slower"), _row(4, "new_clip", "Faster.")],
        photos=[{"photo": 1, "thing": "Black toilet seat", "clear": False}],
    )
    plan = {
        "question": "",
        "scenes": [
            {"scene": 2, "action": "new_picture", "go_back_to": None, "photos": []},
            {"scene": 4, "action": "new_clip", "go_back_to": None, "photos": []},
        ],
    }
    result = {
        "turns": [{"outcome": "planned", "plan": plan, "decide": decided}],
        "looks": [],
        "judge_findings_calls": 0,
    }
    case = {
        "expect": [
            {
                "scenes_include": {"2": "new_picture|new_clip"},
                "scenes": {"2": "new_picture"},
                "scene_cants_include": ["slower"],
                "thing_has": {"1": "seat"},
                "clear": {"1": False},
            }
        ]
    }

    checks = {check["what"]: check["same"] for check in score.score(case, result)}

    # Scene 4 was changed too: fine where other scenes may change, not where they may not.
    assert checks == {
        "scenes_include": True,
        "scenes": False,
        "scene_cants_include": True,
        "thing_has": True,
        "clear": True,
    }


def test_a_fix_check_that_failed_can_be_gone_ahead_on(
    fake_model: FakeModel, ad: Job, tmp_path: Path
) -> None:
    drawn = tmp_path / "scene-2-picture-2.png"
    drawn.write_bytes(_png((0, 0, 0)))
    case: dict[str, Any] = {
        "id": "rule-go-ahead-after-failed-check",
        "ad": "8",
        "setup": {
            "held_checks": [
                {
                    "scene": 2,
                    "picture": str(drawn),
                    "change": "The toilet is black.",
                    "reason": "The toilet still looks grey.",
                }
            ]
        },
        "turns": [{"say": "yes go ahead"}],
        "expect": [{"outcome": "go_ahead", "gone_ahead": [2]}],
    }
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "go_ahead"})]))

    result = record(case, play(ad, case), tmp_path / "run", model="gpt-6.1-sol")

    the_ad = json.loads((tmp_path / "run" / case["id"] / "given.json").read_text())[0]["the_ad"]
    assert "Scene 2's fix check failed on its new picture: The toilet still looks grey." in the_ad
    [went] = result["turns"]
    assert (went["outcome"], went["gone_ahead"]) == ("go_ahead", [2])
    assert all(check["same"] for check in result["checks"])


def test_a_change_that_adds_what_the_owner_never_asked_for_is_caught() -> None:
    plan = {
        "question": "",
        "scenes": [
            {
                "scene": 3,
                "action": "new_clip",
                "change": "The gel goes on the brush first; the scene ends with the bowl clean.",
                "go_back_to": None,
                "photos": [],
            }
        ],
    }
    result = {
        "turns": [{"outcome": "planned", "plan": plan, "decide": _decided([])}],
        "looks": [],
        "judge_findings_calls": 0,
    }
    added = {"expect": [{"change_lacks": {"3": ["ends", "clean"]}}]}
    asked = {"expect": [{"change_lacks": {"3": ["sparkl"]}}]}

    assert [check["same"] for check in score.score(added, result)] == [False]
    assert [check["same"] for check in score.score(asked, result)] == [True]
