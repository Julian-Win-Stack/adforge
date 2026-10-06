"""Count the colour-list test's 5 numbers (issue #110) from the run's results and the photo
judgements, per page and in total, as a Markdown table.

    python3 backend/scratch/colour-list-test/count.py > counts.md"""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
REASONS = [
    "other_colour",
    "other_size",
    "with_others",
    "not_clear",
    "no_product",
    "clear_right_colour",
    "unsure",
]

judged = json.load(open(HERE / "judged.json"))
rows = []
totals: Counter[str] = Counter()
reasons: Counter[str] = Counter()
not_planned = []
cost = 0.0
credits = 0
for file in sorted(OUT.glob("*.json")):
    r = json.load(open(file))
    cost += r.get("model_cost_usd", 0)
    credits += r.get("firecrawl_credits", 0)
    if r.get("status") != "planned":
        not_planned.append((r["key"], r.get("status"), r.get("failed") or r.get("read_page", "")))
        continue
    if r["key"] not in judged:
        raise SystemExit(f"{r['key']} is planned but not judged yet")
    j = judged[r["key"]]
    photos = r["photos"]
    kept, listed = len(photos), sum(p["on_list"] for p in photos)
    dropped = {str(p["position"]) for p in photos if not p["on_list"]}
    assert dropped == set(j["dropped"]), (r["key"], dropped ^ set(j["dropped"]))
    page_reasons = Counter(reason for reason, _, _ in j["dropped"].values())
    good = sum(1 for _, needed, _ in j["dropped"].values() if needed)
    wrong = len(j["wrong_on_list"])
    reasons.update(page_reasons)
    totals.update(kept=kept, listed=listed, good=good, wrong=wrong, pages=1)
    if listed <= 1:
        totals["one_or_none"] += 1
        if kept > 1:
            totals["one_or_none_of_more"] += 1
    rows.append(
        f"| {r['key']} | {r['product_colour']} | {listed}/{kept} | "
        + " | ".join(str(page_reasons[name] or "") for name in REASONS)
        + f" | {good or ''} | {'yes' if listed <= 1 else ''} | {wrong or ''} |"
    )

print("| Page | Plan's colour | On list / kept | " + " | ".join(REASONS) + " | Maybe needed | 0–1 on list | Wrong marks |")
print("|---" * (len(REASONS) + 6) + "|")
print("\n".join(rows))
print(
    f"| **Total ({totals['pages']} pages)** | | **{totals['listed']}/{totals['kept']}** | "
    + " | ".join(f"**{reasons[name]}**" for name in REASONS)
    + f" | **{totals['good']}** | **{totals['one_or_none']}** | **{totals['wrong']}** |"
)
print()
print(f"Dropped: {totals['kept'] - totals['listed']} of {totals['kept']} photos.")
print(f"0 or 1 on the list though more were kept: {totals['one_or_none_of_more']} pages.")
print(f"Not planned: {len(not_planned)}")
for key, status, why in not_planned:
    print(f"- {key} ({status}): {why[:200]}")
print(f"\nModel cost of the final results: ${cost:.2f}; Firecrawl credits: {credits}")
