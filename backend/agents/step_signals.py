"""What the chat is told of each scene's starting picture and clip, so it can tell from the
messages alone when one is being made, and when that ended: a notice as the step starts, the
message showing what it made, or a notice that nothing was. No model is given any of them:
the producer leaves out notices and messages with no words, and learns of each step from its
result."""

from chat import messages
from chat.models import Message
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
    """Tell the chat the step ended with nothing made yet, because its work asked a question
    the producer answers first, if the chat follows the step."""
    ended = signal(step, Message.StepState.NOT_MADE)
    if ended is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    messages.add(
        step.tool_call.session,
        role=Message.Role.NOTICE,
        level=Message.Level.INFO,
        text=f"Scene {ended.scene}'s {name} isn't made yet: a question about it is answered first.",
        step=ended,
    )


def say_not_made(step: SceneStep, why: str) -> None:
    """Tell the chat the step ended with nothing made, if it follows it, and `why` in plain
    words: a fix plan's step tells the shop owner nothing else (case C12b)."""
    ended = signal(step, Message.StepState.NOT_MADE)
    if ended is None:
        return
    _, name = FOLLOWED[SceneStep.Kind(step.kind)]
    messages.add(
        step.tool_call.session,
        role=Message.Role.NOTICE,
        level=Message.Level.PROBLEM,
        text=f"Scene {ended.scene}'s {name} couldn't be made: {why}",
        step=ended,
    )
