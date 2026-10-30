"""What the chat is told of each scene's starting picture and clip, so it can tell from the
messages alone when one is being made, and when that ended: a notice as the step starts, the
message showing what it made, or a notice that nothing was. No model is given any of them:
the producer leaves out notices and messages with no words, and learns of each step from its
result.

Nothing made is the developer's to know, not the shop owner's (spec #272): it is recorded,
and the chat gets a notice with no words, which the scene's card reads its state from and
the chat doesn't show. Only a step the shop owner can help, by sending another photo, is
said in words."""

from chat import messages
from chat.models import Message
from jobs import notices
from jobs.models import SceneStep

# The scene steps the chat follows, and their names in what it is told. A line's audio and
# its transcript are the producer's to judge, so the shop owner isn't told of them.
FOLLOWED = {
    SceneStep.Kind.STARTING_PICTURE: (Message.StepKind.STARTING_PICTURE, "starting picture"),
    SceneStep.Kind.CLIP: (Message.StepKind.CLIP, "clip"),
}


def signal(step: SceneStep, state: Message.StepState) -> messages.StepSignal | None:
    """What a message about `step` says of it, or None for a step the chat doesn't follow."""
    followed = FOLLOWED.get(SceneStep.Kind(step.kind))
    if followed is None:
        return None
    return messages.StepSignal(step.scene.number, followed[0], state)


def say_started(step: SceneStep) -> None:
    """Tell the chat the step started, if it follows it, and whether its scene's picture or
    clip is being made for the first time or made again."""
    started = signal(step, Message.StepState.STARTED)
    if started is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    # One made before was shown, so this one is made again.
    again = step.scene.steps.filter(
        kind=step.kind, status=SceneStep.Status.FINISHED, produced__isnull=False
    ).exists()
    messages.add(
        step.tool_call.session,
        role=Message.Role.NOTICE,
        level=Message.Level.INFO,
        text=f"{'Remaking' if again else 'Making'} scene {started.scene}'s {name}.",
        step=started,
    )


def say_waits_on_a_question(step: SceneStep) -> None:
    """Record that the step ended with nothing made yet, because its work asked a question the
    producer answers first, and tell the scene's card, if the chat follows the step."""
    ended = signal(step, Message.StepState.NOT_MADE)
    if ended is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    _not_made_quietly(
        step,
        ended,
        notices.STEP_WAITS_ON_A_QUESTION,
        f"Scene {ended.scene}'s {name} isn't made yet: a question about it is answered first.",
        Message.Level.INFO,
    )


def say_not_made(step: SceneStep, why: str, *, owner_can_help: bool = False) -> None:
    """Record that the step ended with nothing made, if the chat follows it, and `why` in plain
    words (case C12b), and tell the scene's card. The shop owner is told in red only when
    they `owner_can_help`, as by sending another photo."""
    ended = signal(step, Message.StepState.NOT_MADE)
    if ended is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    text = f"Scene {ended.scene}'s {name} couldn't be made: {why}"
    job = step.scene.job
    if owner_can_help:
        notices.post_notice(
            job, notices.OWNER_FILE_UNUSABLE, text, Message.Level.PROBLEM, step=ended
        )
    else:
        _not_made_quietly(step, ended, notices.STEP_NOT_MADE, text, Message.Level.PROBLEM)


def _not_made_quietly(
    step: SceneStep,
    ended: messages.StepSignal,
    kind: notices.Kind,
    text: str,
    level: Message.Level,
) -> None:
    """Record `text`, a notice of `kind` for the developer, and give the chat the step's end
    in a notice with no words, for the scene's card alone."""
    notices.post_notice(step.scene.job, kind, text, level, step=ended)
    messages.add(
        step.tool_call.session, role=Message.Role.NOTICE, level=Message.Level.INFO, step=ended
    )


def say_cancelled(step: SceneStep) -> None:
    """Tell the chat the step was cancelled by the shop owner's pause before it started, if it
    follows it: nothing was made or paid for."""
    ended = signal(step, Message.StepState.NOT_MADE)
    if ended is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    messages.add(
        step.tool_call.session,
        role=Message.Role.NOTICE,
        level=Message.Level.INFO,
        text=f"Scene {ended.scene}'s {name} was cancelled by the pause before it started.",
        step=ended,
    )
