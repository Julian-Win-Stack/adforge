# Coding standards

## Prompts: no hardcoding

Applies to every prompt the app sends to a model (planner, prompt writer, judges, checks, decider, fixing agent) and to any code that builds those prompts.

- **Rules must be general.** A prompt rule has to help products and shops we never tested, not just the ones that failed.
- **No real failed cases as examples.** Never copy a scene, product or wording from a past failed ad or test into a prompt.
- **No "never X" lists fit to a few cases.** A blanket ban written to stop one or two seen failures is hardcoding.
- **Fix the root cause, not the symptom.** A prompt change must say which general reason made the model fail, and the rule must target that reason.

A reviewer flags any prompt change that breaks one of these as a hard violation.

## Tests: don't pin prompt wording

Tests must not assert sentences or phrases inside a prompt. Test branch logic by the shared
constant's name and wiring by checking that job, owner, page or library data reaches the
prompt. `backend/tests/test_frozen_rules.py` is the one place proven rules are pinned;
those rules change only on Julian's say.
