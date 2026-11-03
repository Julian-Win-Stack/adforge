from agents.scene_forms import PictureForm, picture_prompt


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
        "Image 2 shows the grip. Do not alter the product design. No text."
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
    assert video_prompt(form) == (
        "Film this in real-time speed. Live-action vertical phone footage, handheld UGC, "
        "close-up. A person at a desk. A round blue tin. Locked phone looking down. "
        "The hand grips the lid, then the hand twists it, then the lid lifts. A breath. "
        "The tin keeps its exact shape, colour and printed words. "
        "No speech, murmurs, or vocal sounds."
    )
    reference = form.model_copy(update={"video_for": "copy the twisting movement"})
    assert video_prompt(reference, reference_mode=True) == (
        "Film this in real-time speed. Live-action vertical phone footage, handheld UGC, "
        "close-up. A person at a desk. A round blue tin. Locked phone looking down. "
        "The hand grips the lid, then the hand twists it, then the lid lifts. A breath. "
        "Copy the twisting movement. The clip starts exactly like Image 1. "
        "The tin keeps its exact shape, colour and printed words. "
        "No speech, murmurs, or vocal sounds."
    )


def test_every_optional_box_can_be_left_out_including_shot_and_beats() -> None:
    from agents.scene_forms import EditForm, VideoForm, edit_prompt, video_prompt

    form = VideoForm(
        **{
            name: ["not needed here"] if name == "beats" else "not needed here"
            for name in VideoForm.model_fields
        }
    )
    assert video_prompt(form) == (
        "Film this in real-time speed. Live-action vertical phone footage, handheld UGC. "
        "No speech, murmurs, or vocal sounds."
    )
    assert edit_prompt(
        EditForm(
            keep=["not needed here"], change="not needed here", anything_else="not needed here"
        ),
        [],
    ) == (
        "Edit image 1. Keep everything else as it is in image 1. "
        "Do not alter the product design. No text."
    )
