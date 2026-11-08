import pytest

from agents.scene_forms import PictureForm, VideoForm, picture_prompt


def test_picture_boxes_follow_the_reference_jobs_in_creatify_order() -> None:
    form = PictureForm(
        everything_shown=[],
        camera="  phone at eye level ",
        person_and_room="a person at a desk",
        pose="fingers around the lid",
        inside="not needed here",
        size="fills a quarter",
        moment="hand about to twist",
        light="window on the left",
        bodies="two hands",
        anything_else="not needed here",
    )
    assert picture_prompt(form, ["Image 1 shows the lid.", "Image 2 shows the room."]) == (
        "Image 1 shows the lid. Image 2 shows the room. Phone at eye level. "
        "A person at a desk. Fingers around the lid. Fills a quarter. Hand about to twist. "
        "Window on the left. Two hands. Do not alter the product design. "
        "No retouching, no beauty filter, no text on props, no studio lighting, "
        "no ring light, no bokeh."
    )


def test_edit_keeps_the_list_and_names_one_change_before_reference_jobs() -> None:
    from agents.scene_forms import EditForm, edit_prompt

    form = EditForm(
        keep=["the room", "the grip", "not needed here"],
        change=" move the thumb to the lid ",
        anything_else="not needed here",
    )
    assert edit_prompt(form, ["Image 2 shows the grip."]) == (
        "Edit image 1. Keep the room; the grip exactly the same. "
        "Change only this: Move the thumb to the lid. Keep everything else as it is in image 1. "
        "Image 2 shows the grip. Do not alter the product design."
    )


def test_video_has_code_lines_around_creatify_boxes_and_one_then_between_beats() -> None:
    from agents.scene_forms import VideoForm, video_prompt

    form = VideoForm(
        shot_size="close-up",
        frame_shows="a person at a desk",
        product="a round blue tin",
        camera="locked phone looking down",
        beats=[
            "The hand grips the lid.",
            "Then the hand twists it.",
            "not needed here",
            "And then the lid lifts.",
        ],
        shows_up_later="NOT NEEDED HERE.",
        small_life="a breath",
        anything_else="not needed here",
        lock_line="the tin keeps its exact shape, colour and printed words",
    )
    assert video_prompt(form, line="Get yours.", voice_words="warm") == (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. Live-action vertical phone footage, handheld UGC, "
        "close-up. A person at a desk. A round blue tin. Locked phone looking down. "
        "The hand grips the lid, then the hand twists it, then the lid lifts. A breath. "
        "The tin keeps its exact shape, colour and printed words. "
        "Off camera, the presenter says with warm: <d>[English] Get yours.</d> "
        "Sound: room tone, the action's sounds and the presenter's voice. No music."
    )
    reference = form.model_copy(update={"video_for": "copy the twisting movement"})
    assert video_prompt(reference, reference_mode=True, line="Get yours.", voice_words="warm") == (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. Live-action vertical phone footage, handheld UGC, "
        "close-up. A person at a desk. A round blue tin. Locked phone looking down. "
        "The hand grips the lid, then the hand twists it, then the lid lifts. A breath. "
        "Copy the twisting movement. The clip starts exactly like Image 1. "
        "The tin keeps its exact shape, colour and printed words. "
        "Off camera, the presenter says with warm: <d>[English] Get yours.</d> "
        "Sound: room tone, the action's sounds and the presenter's voice. No music."
    )


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_every_optional_box_can_be_left_out_including_shot_and_beats(kind: str) -> None:
    from agents.scene_forms import EditForm, VideoForm, edit_prompt, video_prompt

    form = VideoForm(
        **{
            name: ["not needed here"] if name == "beats" else "not needed here"
            for name in VideoForm.model_fields
        }
    )
    assert video_prompt(form, line="Get yours.", voice_words="warm", kind=kind) == (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. "
        + (
            "Live-action vertical phone footage, UGC, filmed on a steady phone. "
            "The phone stays perfectly steady the whole time: no sway, no shake. "
            "She talks straight to the lens, and says with warm: <d>[English] Get yours.</d> "
            "Her mouth moves in sync with every word. "
            "Sound: room tone, her clear close voice. No music."
            if kind == "talking"
            else "Live-action vertical phone footage, handheld UGC. "
            "Off camera, the presenter says with warm: <d>[English] Get yours.</d> "
            "Sound: room tone, the action's sounds and the presenter's voice. No music."
        )
    )
    assert edit_prompt(
        EditForm(
            keep=["not needed here"], change="not needed here", anything_else="not needed here"
        ),
        [],
    ) == (
        "Edit image 1. Keep everything else as it is in image 1. Do not alter the product design."
    )
    if kind == "broll":
        assert (
            "Off camera, the presenter says with warm, in a high-energy, excited delivery: "
            "<d>[English] Get yours.</d>"
        ) in video_prompt(form, line="Get yours.", voice_words="warm", kind=kind, hook=True)


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_h3_speech_is_code_owned_with_voice_sound_and_an_action_after_the_line(kind: str) -> None:
    from agents.scene_forms import VideoForm, video_prompt

    form = VideoForm(
        shot_size="medium shot",
        frame_shows="a woman at a table",
        product="a red tin",
        camera="the phone stays still",
        beats=["She opens the tin"],
        shows_up_later="not needed here",
        small_life="a breath",
        lock_line="the tin keeps its shape",
        anything_else="not needed here",
        after_last_word="She raises the open tin towards the lens",
    )
    prompt = video_prompt(
        form, line="Only $10, and it's yours.", voice_words="warm and unhurried", kind=kind
    )
    if kind == "talking":
        assert prompt == (
            "Film this in real-time speed. Movement starts from the very first frame, "
            "never a still opening. Live-action vertical phone footage, UGC, "
            "filmed on a steady phone, medium shot. A woman at a table. A red tin. "
            "The phone stays still. The phone stays perfectly steady the whole time: "
            "no sway, no shake. She opens the tin. "
            "She talks straight to the lens, and says with warm and unhurried: "
            "<d>[English] Only $10, and it's yours.</d> Her mouth moves in sync with every word. "
            "After the last word, she raises the open tin towards the lens. "
            "The tin keeps its shape. Sound: room tone, her clear close voice. No music."
        )
        return
    assert prompt == (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. Live-action vertical phone footage, handheld UGC, "
        "medium shot. A woman at a table. A red tin. The phone stays still. She opens the tin. "
        "A breath. After the last word, she raises the open tin towards the lens. "
        "The tin keeps its shape. "
        "Off camera, the presenter says with warm and unhurried: "
        "<d>[English] Only $10, and it's yours.</d> "
        "Sound: room tone, the action's sounds and the presenter's voice. No music."
    )


def test_talking_delivery_and_sounds_join_lowercase_and_reference_lines_keep_their_place() -> None:
    from agents.scene_forms import VideoForm, video_prompt

    form = VideoForm(
        **{
            name: ["not needed here"] if name == "beats" else "not needed here"
            for name in VideoForm.model_fields
        }
    ).model_copy(
        update={
            "delivery": "Warm and pleased.",
            "sounds": "A kettle hums softly.",
            "after_last_word": "She nods",
            "video_for": "Copy the movement",
            "lock_line": "The tin keeps its shape",
        }
    )
    assert video_prompt(
        form,
        reference_mode=True,
        end_picture=True,
        line="Get yours.",
        voice_words="warm",
        kind="talking",
    ) == (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. Live-action vertical phone footage, UGC, filmed on a steady phone. "
        "The phone stays perfectly steady the whole time: no sway, no shake. "
        "She talks straight to the lens, warm and pleased, and says with warm: "
        "<d>[English] Get yours.</d> Her mouth moves in sync with every word. "
        "After the last word, she nods. Copy the movement. "
        "The clip starts exactly like Image 1. Image 1 is the first frame and Image 2 is the last "
        "frame. The tin keeps its shape. "
        "Sound: room tone, a kettle hums softly, her clear close voice. No music."
    )


@pytest.mark.parametrize(
    ("sounds", "sound_line"),
    [
        (
            "Room tone, a fridge humming softly.",
            "Sound: room tone, a fridge humming softly, her clear close voice. No music.",
        ),
        (
            "  ROOM TONE.  ",
            "Sound: room tone, her clear close voice. No music.",
        ),
    ],
)
def test_talking_sounds_include_room_tone_once(sounds: str, sound_line: str) -> None:
    from agents.scene_forms import VideoForm, video_prompt

    form = VideoForm(
        **{
            name: ["not needed here"] if name == "beats" else "not needed here"
            for name in VideoForm.model_fields
        }
    ).model_copy(update={"sounds": sounds})
    prompt = video_prompt(form, line="Get yours.", voice_words="warm", kind="talking")
    assert prompt.endswith(sound_line)


def test_a_problem_hook_gets_a_free_hand_gesture_and_beats_count_only_product_actions() -> None:
    # Julian (Nov 8 2026, "Design ad hook and emotional after"): in a hook that names a
    # problem her free hand acts it out on her own body; a surprise hook gets none. Only a
    # product action counts toward one action per clip.
    beats = " ".join((VideoForm.model_fields["beats"].description or "").split())
    assert "its one product action (one that moves the product or changes its shape" in beats
    assert "face, head and free-hand moves, which don't count as actions" in beats
    assert "In the hook (part: hook) when its line names a problem, her free hand acts out" in beats
    assert "needs no extra prop and adds or changes nothing in the picture" in beats
    assert "A hook that tells her surprise about the product gets no such gesture" in beats
