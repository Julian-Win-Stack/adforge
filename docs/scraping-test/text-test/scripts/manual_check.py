"""Write manual-check.md: what the improved opener (round 2: open_round2.js, outputs/final/copy-1,
copy-2) got wrong and what it missed, to verify by hand on the live pages. A sentence counts as kept
if either run kept it.
- Wrong: kept sentences the blind judges marked other_product.
- Missed: product sentences we know are on the page but neither improved run kept. "Known to be on
  the page" = an earlier run on the same page kept it and the blind judge marked it this_product
  (outputs/kept.json: pieces-1/2, copy-1/2), or, on the hard pages, the fix test's hand labels.
Usage (host): python3 manual_check.py"""
import collections, contextlib, io, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from textnorm import norm, found
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):  # summary_open prints its table on import
    from summary_open import pages, kept_old, verdicts, new_kept

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "outputs"
FIX = HERE.parent / "fix-test" / "outputs"
CHEWY = "retail-19-chewy-blue-buffalo-dog-food"

# My read of each other-product sentence after looking at it on the saved page.
READ = {
    "pair w food treats": ("food and treats sold separately", "Real, mild: a tip to buy other things"),
    "for decades professionals have relied": ("older DT 770 PRO", "Real: about the older model"),
    "10 count 10 count pads": ("the pads version", "Mild: a size option on this page"),
    "100 eye cream": ("none", "Judge mistake: the concealer's own tagline \"100% Concealer. 100% Eye Cream.\""),
    "experts recommend collars": ("a collar", "Real, mild: harness vs collar advice"),
    "smart camera": ("smart home devices", "Mild: a list of things the remote can control"),
    "fire tv stick 4k plus fire tv stick 4k 1st gen": ("other Fire TV Sticks", "Real: a compatibility list naming other models"),
    "to use wi fi 6 you ll need a compatible router": ("eero Pro 6 router", "Real: names another product"),
    "if you have a wi fi 6e router we recommend": ("Fire TV Stick 4K Max", "Real: recommends a different model"),
    "miniature skillet": ("none", "Judge mistake: Amazon's \"Model Name\" field for this skillet"),
    "choose the right size for your dog": ("other pack sizes", "Mild: size chart for the product's own sizes"),
    "aha exfoliants are great": ("AHA exfoliants", "Real: about a different product type"),
}

# Plain-words summary of what was missed on each page (written from the lists this script prints).
WHAT = {
    "retail-12-bestbuy-sony-xm6": "The whole manufacturer feature section (noise cancelling, sound quality, 30-hour "
        "battery, controls, comfort) and the Q&A (mic mute, USB-C, foldable)",
    "small-02-ouai-detox-shampoo": "The product description, how and when to use it, the ingredient list, the FAQ, the sizes",
    "small-24-our-place-always-pan": "FAQ answers (PFAS-free, 10-in-1, warranty, oven safe), the coating and material "
        "story, cooking features",
    "small-25-graza-sizzle-olive-oil": "The bottle features and the brand story (harvest timing, 13 lbs of olives a "
        "liter, never blended, everyday cooking uses)",
    "small-17-satechi-3in1-qi2-stand": "FAQ answers (charging speeds, Apple Watch, StandBy), case rules, the "
        "compatible-phone lists, size and weight",
    "small-06-necessaire-body-wash": "How to use, warnings, 4 FAQ answers (allergens, no SLS, pregnancy, eczema seal), "
        "the ingredient list, skin types",
    "small-05-kosas-revealer-concealer": "The shade list, the full ingredient list, how to apply, the claims "
        "(brightening, hydrating)",
    "small-01-glossier-boy-brow": "The full ingredient lists, what each key ingredient does, how to apply, the size",
    "other-15-beyerdynamic-dt-770-pro-x": "The technical data table (impedance, weight, ear pads) and FAQ answers "
        "(new driver, detachable cable, comfort)",
    "small-28-wild-one-harness-walk-kit": "Leash and harness sizing advice, the size table, what's in the kit, care "
        "instructions",
    "other-11-kong-classic": "The 6 size dimensions, the description, how to use it (stuff with kibble), safety notes, "
        "material",
    "small-21-brooklinen-luxe-core-sheets": "The size guide, care instructions, the certification number, 2 short claims",
    "small-07-tower28-sos-rescue-spray": "FAQ answers (what hypochlorous acid does, not a setting spray, skin types, "
        "eczema seal, using with actives, when you see results)",
    "other-19-paulaschoice-2pct-bha": "A few FAQ answers (is it a toner, sunscreen), the pH, the ingredient list in one line",
    "small-35-allbirds-mens-tree-runners": "Fit and materials, the size table, weight, heel drop, country of origin, "
        "a press quote",
    "amazon-09-greenies-dental-treats": "FAQ answers (vet recommended, how many a day, texture), barcodes",
    "small-19-moft-snap-on-stand-wallet": "FAQ answers (Apple Pay, angle on different iPhones), weight and thickness",
    "small-11-lmnt-recharge-electrolyte": "The badges (vegan, paleo-keto, no dodgy ingredients) and the potassium and "
        "magnesium amounts",
    "amazon-06-fire-tv-stick-4k-plus": "The sustainability section (carbon footprint) and one Alexa+ line",
    "small-00-mackweldon-crew": "The description (French terry, raglan sleeves), material and care, number of colours",
    "other-16-bose-quietcomfort-ultra": "The tagline and 3 sound-mode descriptions",
    "small-26-fellow-stagg-ekg-kettle": "Weight, the handle and display features, a menu walkthrough title",
    "other-17-zwilling-pro-chefs-knife": "The collection description (made in Germany, pinch grip) and the knife's uses",
    "small-08-liquid-iv-lemon-lime": "The product name, a serving line, \"gluten free, non-GMO\"",
    "small-16-peak-design-everyday-backpack": "3 description sentences (camera bag and everyday bag in one)",
    "small-38-vuori-kore-short-black": "Fit (7\" inseam, liner) and features (pockets, drawcord)",
    "retail-25-macys-levis-501": "The product name, Big & Tall, the material and care line",
    "amazon-02-maybelline-sky-high-mascara": "Amazon's specs and measurements tables and the long product title",
    "amazon-08-lodge-cast-iron-skillet": "Material, capacity, the long product title (oven, stove, grill, campfire)",
    "other-20-barbour-classic-bedale": "The SKU and the material and care information",
    "other-03-nalgene-32oz-wide-mouth": "The colour and one description sentence (BPA-free, made from Tritan Renew)",
    "retail-09-sephora-rare-beauty-blush": "The shade and size line, the highlights (vegan, long-wearing)",
    "amazon-07-stanley-quencher-40oz": "Amazon's one-line product summary",
}

# Known reasons, from results.md and the saved runs.
WHY = {
    "small-06-necessaire-body-wash": "The improved runs took a shorter screenshot of this page (6 slices, against 8 "
        "in an earlier run), so the FAQ at the bottom wasn't in it.",
    "retail-12-bestbuy-sony-xm6": "Best Buy's \"From the Manufacturer\" block is inside a frame the opener can't reach.",
    "small-01-glossier-boy-brow": "Glossier's per-ingredient \"+\" rows have no markings the opener recognises.",
    "small-25-graza-sizzle-olive-oil": "The text is visible on the screenshot; the model just didn't copy most of it.",
}


# Option pickers and buy boxes the judges counted as this_product ("Color: ... Size Chart ... $48"):
# menus, not product text, so not counted as missed.
PICKER = re.compile(r"[$£€]\s?\d|size chart|size guide|select size|add to (cart|bag)|radio button|zoom zoom|"
                    r"report an issue|choose an option|out of stock|\d[\d,]* reviews", re.I)


def dedupe(xs):
    seen, out = set(), []
    for x in xs:
        if norm(x) not in seen: seen.add(norm(x)); out.append(x)
    return out


def phrase(s, words=8):
    return " ".join(s.replace("“", "").replace("”", "").split()[:words])


def secs_of(path):
    pg = json.load(open(path))
    return pg, [(x["heading"].replace("(continued) ", ""), norm(x["text"])) for x in pg["marks"]["secs"]]


truth = json.load(open(FIX / "text_truth.json"))
mini = json.load(open(FIX / "sections-mini.json"))
fix_wrong = {r["id"]: r for r in json.load(open(FIX / "old-wrong-216.json"))}
other_lab = collections.defaultdict(set)
for r in json.load(open(FIX / "old-wrong-216-classified.json")):
    if r["category"] == "other_product": other_lab[fix_wrong[r["id"]]["page"]].add(norm(fix_wrong[r["id"]]["sentence"]))


def labels(key):
    """Hard pages only: the fix test's hand-labelled product sentences (same filter as score_open.py)."""
    if pages[key]["set"] != "hard" or key not in mini: return []
    _, secs = secs_of(OUT / "pages" / f"{key}.json")
    today = " ".join(t for _, t in secs)
    then = norm(" ".join(x["heading"] + " " + x["text"] for x in mini[key]["sections"]))
    return [r["text"] for r in truth if r["key"] == key and r["truth"] == "product_info"
            and norm(r["text"]) not in other_lab[key] and found(r["text"], today) and found(r["text"], then)]


def name(key):
    return pages[key]["product"]


def link(key):
    return f"[{name(key)}]({pages[key]['url']})"


new = {k: dedupe(new_kept(k, 1) + new_kept(k, 2)) for k in pages}
new_text = {k: norm(" ".join(v)) for k, v in new.items()}

missed = {}  # key -> [(section, [sentences])]
for key in pages:
    if key == CHEWY: continue
    v = verdicts(key)
    known = [s for src in ("copy-1", "copy-2", "pieces-1", "pieces-2") for s in kept_old.get(key, {}).get(src, [])
             if v.get(norm(s)) == "this_product"] + labels(key)
    miss = []
    for s in dedupe(known):
        if PICKER.search(s): continue
        if not found(s, new_text[key]) and not found(s, norm(" ".join(miss))): miss.append(s)
    if not miss: continue
    secs = secs_of(OUT / "final" / "pages" / f"{key}.json")[1] + secs_of(OUT / "pages" / f"{key}.json")[1]
    g = collections.defaultdict(list)
    for s in miss: g[next((h for h, t in secs if found(s, t)), "other places on the page")].append(s)
    missed[key] = sorted(g.items(), key=lambda kv: -len(kv[1]))

count = lambda k: sum(len(x) for _, x in missed[k])
order = sorted(missed, key=lambda k: -count(k))
tot_miss = sum(count(k) for k in order)

wrong = []
for key in sorted(pages):
    v = verdicts(key)
    wrong += [(key, s) for s in new[key] if v.get(norm(s)) == "other_product"]
reads = [next((r for p, r in READ.items() if norm(s).startswith(p)), ("?", "?")) for _, s in wrong]
n_mistake = sum(r[1].startswith("Judge mistake") for r in reads)
n_real = sum(r[1].startswith("Real") for r in reads)
n_mild = len(wrong) - n_mistake - n_real

L = ["# Improved opener: what it got wrong and what it missed", "",
     "The improved opener (`scripts/open_round2.js`) opens closed tabs, \"Read more\" buttons and FAQ questions "
     "before the screenshot. It was run twice on 34 product pages (Chewy didn't load). A sentence counts as "
     "extracted if either run extracted it.", "",
     "## The answer in short", "",
     f"1. **Other products' info: {len(wrong)} sentences on {len({k for k, _ in wrong})} pages.** "
     f"{n_mistake} are judge mistakes (they are about this product), {n_mild} are mild (size options, a list of "
     f"smart-home devices), and **{n_real} are really about another product**, most of them on Fire TV. See Part 1.",
     f"2. **Missed: {tot_miss} product sentences on {len(order)} pages.** "
     "We know they are on the page because another test run on the same page found them, or because they are "
     "in our hand-made list of each hard page's text. See Part 2.", "",
     "## How to check", "",
     "1. Click the page link.",
     "2. Press **Cmd+F** and paste the phrase or the first few words of the sentence. "
     "If it isn't found, open the page's closed tabs and sections (Details, Ingredients, FAQ...) and search again.",
     "3. For Part 1, ask: if this sentence went into an ad for this product, would it mislead someone? "
     "For Part 2, ask: is this text really on the page, and is it about this product?", "",
     "If Cmd+F finds nothing on Amazon, search only the last 3 or 4 words: Amazon puts a label and its value "
     "(\"Product Dimensions\" and \"5.82\"W x 12.3\"H\") in separate table cells.", "",
     "## Part 1: other products' info it extracted", "",
     "| Page | Search for | Other product | My read |", "|---|---|---|---|"]
L += [f"| {link(k)} | `{phrase(s).replace('|', '/')}` | {r[0]} | {r[1]} |" for (k, s), r in zip(wrong, reads)]
L += ["", "## Part 2: what it missed", "",
      "| # | Page | Sentences missed | What it is |", "|---|---|---|---|"]
L += [f"| {i} | {link(k)} | {count(k)} | {WHAT.get(k, '?')} |" for i, k in enumerate(order, 1)]
L += ["", "Nothing missed: " + (", ".join(name(k) for k in sorted(pages) if k not in missed and k != CHEWY) or "none")
      + ".", ""]
for i, k in enumerate(order, 1):
    L += [f"### {i}. {name(k)}: {count(k)} missed", "", pages[k]["url"], ""]
    if k in WHY: L += [f"Why: {WHY[k]}", ""]
    for h, xs in missed[k]:
        L += [f"- Section *{h[:60]}*:"] + [f"    - {x}" for x in xs]
    L += [""]
(HERE / "manual-check.md").write_text("\n".join(L))
print(f"wrong {len(wrong)} (real {n_real}, mild {n_mild}, mistakes {n_mistake}); missed {tot_miss} on {len(order)} pages")
