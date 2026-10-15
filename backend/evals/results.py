"""Counts from one graded run of ads: how many ads and B-roll clips Julian passed on the first
try and after the clip judge's auto-redo, the judge against his grades, what the redo fixed,
why clips failed, and what each ad cost. Free: it reads files, it calls no model. Counts, not
percentages, and only what a file backs; anything a run didn't record is said so. From
backend/:

    uv run python -m evals.results <run folder or run.json>

THE RECORD SHAPE a run keeps, so every round prints the same numbers (round 4 is the first,
round 5 must keep it):

run.json  {"name": "Round 5", "ad_grade_is": "first" | "kept", "grades": "grades.json",
           "judge": "results.json", "taps": null | "taps.json", "costs": null | "costs.json",
           "ad_folders": {"<ad id in judge keys>": "<ad folder>", ...},
           "regrades": null | "regrades.json"}
    Paths are relative to run.json. "ad_grade_is" says which clips the whole ad he graded
    holds: "first" when the ad was made without the auto-redo (round 4), "kept" when the app
    ran it (round 5).
grades.json  his taps, one entry per graded thing ({"grade": "pass" | "fail", "note": his
    words, "cause": the first step that broke, on a failed clip, "at": when}):
    "<ad folder>__whole-ad", "<ad folder>__scene-N" (the scene's FIRST clip) and
    "<ad folder>__scene-N__kept" (the clip the auto-redo kept, only when it kept a remake).
    "cause" is one of CAUSES; there's no "judge" cause: the judge's misses are counted here
    from its saved verdicts.
results.json  the judge and the auto-redo per B-roll scene, keyed "<ad id>:<N>":
    {"tries": [{"remake": null | 1 | 2, "ruling": {"verdict": "pass" | "fail" |
    "not_checked", "wording": ...}}, ...], "kept": {"remake": null | k}, "costs": {"total":
    dollars for the judge and the remakes}, "fix_check": {"verdict"} (optional)}. tries[0]
    is the first clip. evals/round_export.py writes it from the app; round 4's came from
    runs/round-4-loop-test/run_loop.py.
regrades.json  (optional) {"<grades.json key>": {"now": "fail", "julian": his words}}: a
    grade changed after he tapped it, applied before anything is counted. Where his note says
    the judge was right and he was wrong, his grade is FAIL (Julian, 2026-10-14 21:12): a
    session reading his notes writes that here, so a pass tap with such a note never counts
    as the judge's wrong fail.
taps.json  (optional, round 4's loop test) {"<ad folder>__scene-N": {"fixed_K": "yes" |
    "partly" | "no", "fix_check_agree": "yes" | "no"}}. The fix check was right when its
    verdict and his "fixed" tap agree (passed/yes, failed/no), wrong when they're opposite
    (passed/no, failed/yes); on "partly" his agree tap decides.
costs.json  (optional) {"source": where the dollars come from, "ads": {"<ad folder>":
    {"total_usd": all model calls, "video_usd": the paid video calls}}}.

Judge against his grades follows evals/clip-judge-v2/score.py (project files): a catch is a
clip he failed that the judge failed, a wrong fail a clip he passed that the judge failed, and
a clip the judge couldn't check is neither.
"""

import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAUSES = {
    "planner": "the plan asked for the wrong or an impossible thing",
    "start_picture": "the start picture",
    "motion_prompt": "the motion prompt",
    "video_model": "the video model did the order badly",
}
FIXED = ("yes", "partly", "no")
_SCENE = re.compile(r"^(?P<ad>.+)__scene-(?P<n>\d+)(?P<kept>__kept)?$")
_WHOLE = re.compile(r"^(?P<ad>.+)__whole-ad$")


def grade_key(ad: str, number: int | None = None, kept: bool = False) -> str:
    """The grades.json key of an ad's whole-ad grade, or of a scene's first or kept clip."""
    if number is None:
        return f"{ad}__whole-ad"
    return f"{ad}__scene-{number}" + ("__kept" if kept else "")


@dataclass
class Grade:
    grade: str
    note: str = ""
    cause: str | None = None


@dataclass
class Scene:
    """One B-roll scene of an ad: his grades and what the judge and the auto-redo did."""

    ad: str
    number: int
    first: Grade | None = None
    kept: Grade | None = None
    judge_first: str | None = None
    remakes: int = 0
    kept_remake: int | None = None
    judge_kept: str | None = None
    redo_cost: float | None = None
    fixed: str | None = None
    fix_check: str | None = None
    fix_check_agree: str | None = None

    @property
    def name(self) -> str:
        return f"{self.ad} s{self.number}"

    @property
    def kept_a_remake(self) -> bool:
        return self.kept_remake is not None

    @property
    def final(self) -> Grade | None:
        """His grade on the clip the ad ends up with: the kept remake's, else the first's."""
        return self.kept if self.kept_a_remake else self.first

    @property
    def fix_check_right(self) -> bool | None:
        """Whether the fix check's verdict matched his tap on the kept remake; None when
        neither his "fixed" tap nor his agree tap settles it."""
        said = {"passed": "yes", "failed": "no"}.get(self.fix_check or "")
        if said is None or self.fixed is None:
            return None
        if self.fixed != "partly":
            return self.fixed == said
        return {"yes": True, "no": False}.get(self.fix_check_agree or "")


@dataclass
class Run:
    name: str
    ad_grade_is: str
    ads: dict[str, Grade | None]
    scenes: list[Scene]
    costs: dict[str, dict[str, float]] = field(default_factory=dict)
    cost_source: str = ""
    wordings: list[str] = field(default_factory=list)
    has_judge: bool = False
    has_taps: bool = False


def _read(base: Path, path: str | None) -> Any:
    return json.loads((base / path).read_text()) if path else None


def _grade(entry: dict[str, Any] | None) -> Grade | None:
    if not entry or entry.get("grade") not in ("pass", "fail"):
        return None
    return Grade(entry["grade"], entry.get("note") or "", entry.get("cause") or None)


def _verdict(clip: dict[str, Any] | None) -> str | None:
    return ((clip or {}).get("ruling") or {}).get("verdict")


def load_run(where: Path) -> Run:
    """The run whose run.json is `where` (or in the folder `where`)."""
    run_file = where / "run.json" if where.is_dir() else where
    base = run_file.parent
    meta = json.loads(run_file.read_text())
    grades: dict[str, Any] = _read(base, meta["grades"]) or {}
    judged: dict[str, Any] = _read(base, meta.get("judge")) or {}
    taps: dict[str, Any] = _read(base, meta.get("taps")) or {}
    costs: dict[str, Any] = _read(base, meta.get("costs")) or {}
    folders: dict[str, str] = meta.get("ad_folders") or {}
    regrades: dict[str, Any] = _read(base, meta.get("regrades")) or {}
    for key, regrade in regrades.items():
        grades[key] = {**grades.get(key, {}), "grade": regrade["now"]}

    scenes: dict[tuple[str, int], Scene] = {}
    ads: dict[str, Grade | None] = {}

    def scene(ad: str, number: int) -> Scene:
        ads.setdefault(ad, None)
        return scenes.setdefault((ad, number), Scene(ad, number))

    for key, entry in grades.items():
        if whole := _WHOLE.match(key):
            ads[whole["ad"]] = _grade(entry)
        elif match := _SCENE.match(key):
            s = scene(match["ad"], int(match["n"]))
            if match["kept"]:
                s.kept = _grade(entry)
            else:
                s.first = _grade(entry)

    wordings: list[str] = []
    for key, result in judged.items():
        ad_id, number = key.rsplit(":", 1)
        s = scene(folders.get(ad_id, ad_id), int(number))
        tries = result.get("tries") or []
        if not tries:
            continue
        s.judge_first = _verdict(tries[0])
        s.remakes = len(tries) - 1
        kept = result.get("kept") or {}
        s.kept_remake = kept.get("remake")
        s.judge_kept = _verdict(kept) or next(
            (_verdict(t) for t in tries if t.get("remake") == s.kept_remake), None
        )
        s.redo_cost = (result.get("costs") or {}).get("total")
        s.fix_check = (result.get("fix_check") or {}).get("verdict")
        for clip in tries:
            wording = (clip.get("ruling") or {}).get("wording")
            if wording and wording not in wordings:
                wordings.append(wording)

    for key, tap in taps.items():
        if (match := _SCENE.match(key)) and (match["ad"], int(match["n"])) in scenes:
            s = scenes[(match["ad"], int(match["n"]))]
            if s.kept_a_remake:
                s.fixed = tap.get(f"fixed_{s.kept_remake}")
            s.fix_check_agree = tap.get("fix_check_agree")

    return Run(
        name=meta.get("name") or base.name,
        ad_grade_is=meta.get("ad_grade_is", "kept"),
        ads=ads,
        scenes=sorted(scenes.values(), key=lambda s: (list(ads).index(s.ad), s.number)),
        costs=costs.get("ads") or {},
        cost_source=costs.get("source") or "",
        wordings=wordings,
        has_judge=bool(judged),
        has_taps=bool(taps),
    )


def _names(scenes: Iterable[Scene]) -> str:
    names = [s.name for s in scenes]
    return f" ({', '.join(names)})" if names else ""


def _judge_lines(
    scenes: list[Scene],
    grade_of: Callable[[Scene], Grade | None],
    verdict_of: Callable[[Scene], str | None],
) -> list[str]:
    """His fails the judge caught or missed, his passes it wrongly failed: score.py's terms."""
    graded = [(s, g) for s in scenes if (g := grade_of(s))]
    fails = [s for s, g in graded if g.grade == "fail"]
    passes = [s for s, g in graded if g.grade == "pass"]

    def judged(group: list[Scene], verdict: str | None) -> list[Scene]:
        return [s for s in group if verdict_of(s) == verdict]

    unjudged = [s for s, _ in graded if verdict_of(s) not in ("pass", "fail", "not_checked")]
    lines = [
        f"- Clips he failed: {len(fails)}. The judge caught {len(judged(fails, 'fail'))}"
        f"{_names(judged(fails, 'fail'))}, missed {len(judged(fails, 'pass'))}"
        f"{_names(judged(fails, 'pass'))}, couldn't check {len(judged(fails, 'not_checked'))}"
        f"{_names(judged(fails, 'not_checked'))}.",
        f"- Clips he passed: {len(passes)}. The judge wrongly failed "
        f"{len(judged(passes, 'fail'))}{_names(judged(passes, 'fail'))}, agreed on "
        f"{len(judged(passes, 'pass'))}, couldn't check {len(judged(passes, 'not_checked'))}"
        f"{_names(judged(passes, 'not_checked'))}.",
    ]
    if unjudged:
        lines.append(f"- Graded but never judged: {len(unjudged)}{_names(unjudged)}.")
    return lines


def _count(grades: Iterable[Grade | None]) -> tuple[int, int, int]:
    """(graded, passed, failed)."""
    given = [g for g in grades if g]
    passed = sum(g.grade == "pass" for g in given)
    return len(given), passed, len(given) - passed


def report(run: Run) -> str:
    """The run's numbers as a markdown section."""
    scenes = run.scenes
    by_ad: dict[str, list[Scene]] = {ad: [s for s in scenes if s.ad == ad] for ad in run.ads}
    out = [f"## {run.name}: numbers", ""]
    out.append(
        "Graded by Julian. "
        + (
            f"Judge wording: {', '.join(run.wordings)}."
            if run.wordings
            else "No judge verdicts saved in this run."
        )
    )

    # Whole ads. An ad is bad when any clip it holds is bad, whatever its whole-ad tap says.
    def held(scene: Scene) -> Grade | None:
        return scene.first if run.ad_grade_is == "first" else scene.final

    whole = {ad: grade.grade for ad, grade in run.ads.items() if grade}
    graded_ads = list(whole)
    clip_failed = [
        ad
        for ad in graded_ads
        if whole[ad] == "pass" and any((g := held(s)) and g.grade == "fail" for s in by_ad[ad])
    ]
    good = [ad for ad in graded_ads if whole[ad] == "pass" and ad not in clip_failed]
    holds = (
        "the first clips (made without the auto-redo)"
        if run.ad_grade_is == "first"
        else "the clips the auto-redo kept"
    )
    out += [
        "",
        "### Whole ads",
        f"- Ads graded: {len(graded_ads)}, good {len(good)}, bad {len(graded_ads) - len(good)}. "
        f"These ads hold {holds}.",
    ]
    if clip_failed:
        out.append(
            f"- Counted bad though he passed the whole ad, as a clip it holds failed: "
            f"{len(clip_failed)} ({', '.join(clip_failed)})."
        )

    def all_good(ad: str, grade_of: Callable[[Scene], Grade | None]) -> bool | None:
        """False when a clip failed; None when none failed but one isn't graded."""
        grades = [grade_of(s) for s in by_ad[ad]]
        if any(g and g.grade == "fail" for g in grades):
            return False
        if not grades or any(g is None for g in grades):
            return None
        return True

    for grade_of, label in (
        (lambda s: s.first, "first clip"),
        (lambda s: s.final, "final clip (after the auto-redo)"),
    ):
        known = {ad: all_good(ad, grade_of) for ad in run.ads}
        told = [ad for ad, good_ad in known.items() if good_ad is not None]
        missing = len(run.ads) - len(told)
        line = (
            f"- Ads where he passed every B-roll {label}: "
            f"{sum(bool(known[ad]) for ad in told)} of {len(told)}"
        )
        out.append(line + (f" ({missing} ads have a clip not graded)." if missing else "."))

    # B-roll clips.
    graded, passed, failed = _count(s.first for s in scenes)
    judge_failed = [s for s in scenes if s.judge_first == "fail"]
    remade = [s for s in scenes if s.kept_a_remake]
    out += [
        "",
        "### B-roll clips",
        f"- First clips graded: {graded}, passed {passed}, failed {failed}.",
    ]
    if run.has_judge:
        out.append(
            f"- The judge failed {len(judge_failed)} first clips; the auto-redo made "
            f"{sum(s.remakes for s in scenes)} remakes and kept a remake in {len(remade)} "
            f"scenes{_names(remade)}."
        )
        k_graded, k_passed, k_failed = _count(s.kept for s in remade)
        if k_graded:
            out.append(f"- Kept remakes graded: {k_graded}, passed {k_passed}, failed {k_failed}.")
        else:
            out.append("- Kept remakes graded pass or fail: none in this run.")
        f_graded, f_passed, _ = _count(s.final for s in scenes)
        out.append(
            f"- Final clips (the kept remake, else the first clip) he passed: {f_passed} of "
            f"{f_graded} graded."
        )
        good_remade = [s for s in remade if s.first and s.first.grade == "pass"]
        broken = [s for s in good_remade if s.kept and s.kept.grade == "fail"]
        out.append(
            f"- Clips he passed that the auto-redo swapped for a remake: {len(good_remade)}"
            f"{_names(good_remade)}; of those, kept remakes he failed: {len(broken)}"
            f"{_names(broken)}."
        )
        fixed_bad = [s for s in remade if s.first and s.first.grade == "fail" and s.kept]
        if fixed_bad:
            now_good = [s for s in fixed_bad if s.kept and s.kept.grade == "pass"]
            out.append(
                f"- Clips he failed that the auto-redo replaced and he graded again: "
                f"{len(fixed_bad)}; the kept remake passed in {len(now_good)}{_names(now_good)}."
            )
    else:
        out.append("- No judge or auto-redo record in this run.")
    if run.has_taps:
        tapped = Counter(s.fixed for s in remade if s.fixed)
        checked = [s for s in remade if s.fix_check]
        right = [s for s in checked if s.fix_check_right is True]
        wrong = [s for s in checked if s.fix_check_right is False]
        out.append(
            "- Did the kept remake fix the judge's complaint (his taps): "
            + ", ".join(f"{word} {tapped[word]}" for word in FIXED)
            + f"; not tapped {len(remade) - sum(tapped.values())}."
        )
        out.append(
            f"- Fix check (fixed or not, kept remake against the first clip), by his taps: right "
            f"{len(right)}, wrong {len(wrong)}{_names(wrong)}, not settled "
            f"{len(checked) - len(right) - len(wrong)}."
        )

    # Judge against his grades.
    out += ["", "### Judge against his grades, first clips"]
    out += (
        _judge_lines(scenes, lambda s: s.first, lambda s: s.judge_first)
        if run.has_judge
        else ["- No judge verdicts in this run."]
    )
    if any(s.kept for s in remade):
        out += ["", "### Judge against his grades, kept remakes"]
        out += _judge_lines(remade, lambda s: s.kept, lambda s: s.judge_kept)

    # Causes.
    failed_clips = [g for s in scenes for g in (s.first, s.kept) if g and g.grade == "fail"]
    causes = Counter(g.cause for g in failed_clips if g.cause in CAUSES)
    out += ["", "### First step that broke (his tap per failed clip)"]
    if not any(g.cause for g in failed_clips):
        out.append("- Not recorded in this run.")
    else:
        out += [f"- {CAUSES[cause]}: {causes[cause]}" for cause in CAUSES]
        out.append(f"- Failed clips with no tap: {len(failed_clips) - sum(causes.values())}")

    # Cost.
    out += ["", "### Cost"]
    if run.costs:
        priced_ads = [ad for ad in run.ads if ad in run.costs]
        out += [f"- {ad}: ${run.costs[ad]['total_usd']:.2f}" for ad in priced_ads]
        total = sum(run.costs[ad]["total_usd"] for ad in priced_ads)
        out.append(
            f"- All {len(priced_ads)} ads (sum of the figures above): ${total:.2f}."
            + (f" Source: {run.cost_source}." if run.cost_source else "")
        )
    else:
        out.append("- No cost record in this run.")
    if run.has_judge:
        priced = [s for s in scenes if s.redo_cost is not None]
        out.append(
            f"- Judge and remakes, all {len(priced)} B-roll scenes: "
            f"${sum(s.redo_cost or 0 for s in priced):.2f}."
        )
    return "\n".join(out) + "\n"


def main(args: list[str]) -> None:
    for where in args:
        print(report(load_run(Path(where))))


if __name__ == "__main__":
    main(sys.argv[1:])
