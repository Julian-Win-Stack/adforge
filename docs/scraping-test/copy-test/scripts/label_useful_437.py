"""Hand labels for the 437 product_info rows of text_truth.json.

Each product_info row (by 0-based index in the list) gets one label:
useful / not_useful / unclear, plus a short reason. Labels were made by reading
the sentence and its neighbouring rows on the same page, blind to any method's
outputs. Writes outputs/useful_437.json, then checks coverage.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TRUTH = ROOT / "docs/scraping-test/fix-test/outputs/text_truth.json"
OUT = ROOT / "docs/scraping-test/copy-test/outputs/useful_437.json"

U, N, Q = "useful", "not_useful", "unclear"

# (label, why, [indices])
GROUPS = [
    # --- ilia
    (N, "Single shade variant name", [81, 87]),
    (U, "Consumer study result", [88, 89, 90]),
    # --- kosas / necessaire
    (U, "Product size", [193]),
    (U, "Size option with fragrance-free claim", [228, 229, 230]),
    # --- tower28
    (N, "Image alt text describing a photo", [283, 284, 285, 286, 287, 288, 289, 290, 291, 292, 296, 308, 314]),
    (U, "Bundle size and contents", [293, 294]),
    (U, "Key claim: recognised by skin organisations", [295]),
    # --- liquid iv / lmnt
    (N, "Recipe ingredient line from social post", [346]),
    (U, "Flavour description: most popular, salty staple", [418]),
    # --- ritual
    (U, "Serving size", [401]),
    (U, "Ingredient and source", [402, 404, 405]),
    (U, "Vitamin D dose (supplement facts cell)", [403, 406]),
    # --- momentous
    (U, "Purity / testing claim", [437, 438, 439, 440]),
    (Q, "Test count; may belong to whey upsell card above", [448]),
    (U, "What it does: muscle + cognitive support", [485]),
    # --- cymbiotika
    (U, "Video blurb stating benefit claims", [496, 499, 505, 506, 507, 508, 509]),
    (U, "Ingredient description", [510, 514, 518, 519, 522, 523]),
    (U, "Ingredient health benefit claim", [512, 513, 515, 516, 517, 520, 521, 524, 525]),
    (U, "Benefit claim", [526, 538, 567, 611]),
    (U, "Expert review: mechanism / research claim", [528, 529, 530, 531, 540, 541, 542, 612, 613, 614]),
    # --- bloom / satechi
    (U, "Product name and tagline", [622]),
    (U, "Warranty", [658]),
    # --- moft
    (N, "Bare colour variant list", [691, 703, 709]),
    (U, "Spec", [697, 698]),
    # --- rokform
    (U, "Feature: lanyard holes", [734]),
    # --- brooklinen
    (U, "Product name", [825]),
    (U, "What's included", [827, 839, 840, 841]),
    (N, "Selected colour name", [828]),
    (U, "Press award / accolade", [833]),
    (U, "Benefit / feel claim", [834, 836, 837, 838, 850, 921, 922, 923]),
    (U, "Material / construction detail", [835, 842, 843, 844, 846, 847, 848, 849]),
    (U, "Care instructions", [851, 852, 853, 854]),
    (U, "Brand FAQ answer: buying pieces separately", [930]),
    (U, "Fit: mattress depth", [937, 938]),
    (N, "Bare pattern/colour name, other products", [941, 942, 943, 944, 945, 946, 947, 948, 949, 950]),
    # --- caraway / parachute
    (U, "What the set is", [985]),
    (N, "Bare closure-option variant list", [994]),
    # --- our place
    (U, "Product name / tagline", [1005, 1008, 1009, 1019, 1029, 1030]),
    (U, "Design / non-toxic claim", [1006, 1007, 1020, 1021, 1022, 1023]),
    (U, "What's included in bundle", [1010, 1013, 1015]),
    (U, "Third-party testing claim", [1024, 1025, 1026]),
    # --- graza
    (U, "What it is", [1083, 1112]),
    (U, "Brand claim / story copy", [1093, 1094, 1102, 1103, 1104, 1105, 1106, 1107, 1108, 1109, 1110]),
    (U, "Bottle feature", [1095, 1096, 1097]),
    (N, "Section heading with no content", [1098]),
    # --- thorne
    (U, "Product name + certification", [1121]),
    (U, "What it is / benefit claim", [1122, 1123, 1124, 1125, 1144, 1145, 1146, 1147]),
    (N, "Section heading with no content", [1126]),
    (U, "Ingredient benefit claim", [1127, 1128, 1129, 1130, 1131, 1132, 1133, 1134]),
    (U, "Research / why-a-multi argument", [1135, 1136, 1137, 1138, 1139, 1140, 1141, 1142]),
    (U, "NSF Certified for Sport claim", [1143]),
    # --- wild one
    (N, "Bare colour/size variant list", [1167, 1196, 1197]),
    (N, "Generic sizing-guide intro, no fact", [1182]),
    (U, "Sizing / how to use", [1188, 1189, 1190]),
    (Q, "Weight cell; unclear which size it belongs to", [1191]),
    # --- fable
    (U, "What it is / benefit claim", [1201, 1202, 1203]),
    (U, "Safe-use guidance", [1204, 1205, 1206, 1207, 1208]),
    (U, "Vet endorsement quote", [1209, 1210]),
    (U, "Dimensions / capacity / food size", [1211, 1212, 1213, 1214]),
    # --- petlibro / allbirds
    (N, "Bare variant name", [1361]),
    (N, "Bare colour name list", [1472, 1473]),
    # --- cotopaxi
    (U, "Tagline / what it is", [1482, 1487, 1514, 1515]),
    (U, "Feature / spec", [1488, 1489, 1490, 1491, 1492, 1493, 1494, 1495, 1496, 1497, 1498, 1516, 1517]),
    (N, "Contact instruction, not product info", [1499]),
    (U, "Materials", [1500, 1501, 1502]),
    (U, "Volume / size / weight", [1503, 1504, 1505]),
    (U, "Care instructions", [1506, 1507, 1508]),
    (N, "Legal Prop 65 notice, not ad material", [1509, 1510, 1511]),
    (U, "Guarantee / fair-trade claim", [1524, 1525, 1526]),
    (N, "Generic site-wide reassurance line", [1555, 1557]),
    # --- vuori / ulta
    (U, "Benefit tagline", [1570, 1574]),
    (U, "Product name", [1680, 1688]),
    # --- amazon maybelline / fire tv
    (N, "Section heading with no content", [1992]),
    (U, "Sustainability / EWG certification claim", [1993, 1994, 1995]),
    (U, "Feature: Alexa+ support", [2846, 2847]),
    (Q, "Carbon footprint; fact but unlikely ad material", [2877]),
    # --- nopong
    (U, "Product name / size", [3138, 3142]),
    (U, "Clinically tested claim", [3139]),
    # --- variant dropdowns
    (N, "Bare variant dropdown list", [3195, 3197, 3198, 3210, 3264, 3320]),
    # --- bulk nutrients
    (U, "Ingredients", [3223]),
    (U, "Serving size / servings per pack", [3227, 3228]),
    # --- kong
    (U, "Size by dog weight", [3321, 3322]),
    # --- bose
    (U, "Feature tagline", [3382, 3396]),
    # --- zwilling
    (U, "Product name", [3404]),
    (U, "Spec: edge angle", [3452, 3477]),
    (N, "About Santoku-style blades, not this knife", [3453]),
    (U, "Brand FAQ answer: knife block fit", [3459, 3460]),
    (U, "Weight, balance, comfort", [3463, 3464, 3465]),
    (Q, "Handle-colour answer; question lost, little value", [3475]),
    # --- life extension
    (U, "Capsule count", [3485]),
    (U, "Ingredient benefit claim", [3496, 3497, 3498, 3499, 3500, 3501]),
    (U, "Brand Q&A answer: product fact", [3544, 3545, 3551, 3552, 3555, 3556, 3562, 3563, 3564,
                                           3567, 3570, 3571, 3573, 3576, 3580, 3586, 3592, 3593]),
    (N, "General nutrient note + 'Was this helpful' UI", [3548]),
    (N, "Generic disclaimer / see-your-doctor line", [3557, 3558, 3572, 3581]),
    (Q, "Points to another product (One-Per-Day)", [3577]),
    (N, "General K2 fact; product has no K2", [3584, 3585]),
    # --- paula's choice
    (U, "What it does / benefit claim", [3631, 3632, 3633, 3634, 3635, 3636, 3637, 3638, 3639, 3640, 3641, 3642]),
    (U, "Claim substantiation footnote", [3643]),
    (U, "Packaging: recycled plastic, #1 bestseller", [3644]),
    (U, "How to use", [3645, 3646, 3647, 3648, 3649, 3651]),
    (U, "Safety caution for use", [3650]),
    (U, "Ingredient description / benefit", [3657, 3658, 3659, 3660, 3661, 3662, 3663, 3664, 3665, 3666]),
    (U, "Award / accolade", list(range(3670, 3696))),
    (U, "Brand FAQ question; answer follows", [3696, 3700, 3703, 3706, 3710, 3725, 3728,
                                              3732, 3734, 3736, 3741, 3743, 3749, 3752, 3755]),
    (U, "Brand FAQ answer about this product", [3697, 3698, 3701, 3704, 3705, 3707, 3711, 3726, 3727,
                                               3729, 3730, 3733, 3735, 3737, 3738, 3739, 3740, 3742,
                                               3744, 3745, 3746, 3747, 3748, 3750, 3751, 3753, 3754,
                                               3756, 3757, 3758, 3759]),
    (N, "About AHA / other toners, not this product", [3699, 3708]),
    (N, "Filler line, no product fact", [3702, 3709, 3712, 3713, 3731]),
    (Q, "Comparison question; answer mostly other products", [3715]),
    (N, "Intro to list of other BHA products", [3716]),
    (Q, "Describes one of four BHA products; header missing", [3717, 3718]),
    (N, "Generic category blurb from skin-goals section", [3768]),
    (Q, "Carousel card claim; may belong to another product", [3772, 3773, 3774, 3775, 3776]),
]


def main():
    rows = json.loads(TRUTH.read_text())
    out, seen = [], {}
    for label, why, idxs in GROUPS:
        assert len(why.split()) < 15, why
        for i in idxs:
            assert i not in seen, f"duplicate index {i}"
            seen[i] = True
            out.append({"i": i, "key": rows[i]["key"], "label": label, "why": why})
    out.sort(key=lambda r: r["i"])
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(out)} rows to {OUT}")


if __name__ == "__main__":
    main()
