# Improved opener: what it got wrong and what it missed

The improved opener (`scripts/open_round2.js`) opens closed tabs, "Read more" buttons and FAQ questions before the screenshot. It was run twice on 34 product pages (Chewy didn't load). A sentence counts as extracted if either run extracted it.

## The answer in short

1. **Other products' info: 12 sentences on 8 pages.** 2 are judge mistakes (they are about this product), 3 are mild (size options, a list of smart-home devices), and **7 are really about another product**, most of them on Fire TV. See Part 1.
2. **Missed: 362 product sentences on 33 pages.** We know they are on the page because another test run on the same page found them, or because they are in our hand-made list of each hard page's text. See Part 2.

## How to check

1. Click the page link.
2. Press **Cmd+F** and paste the phrase or the first few words of the sentence. If it isn't found, open the page's closed tabs and sections (Details, Ingredients, FAQ...) and search again.
3. For Part 1, ask: if this sentence went into an ad for this product, would it mislead someone? For Part 2, ask: is this text really on the page, and is it about this product?

If Cmd+F finds nothing on Amazon, search only the last 3 or 4 words: Amazon puts a label and its value ("Product Dimensions" and "5.82"W x 12.3"H") in separate table cells.

## Part 1: other products' info it extracted

| Page | Search for | Other product | My read |
|---|---|---|---|
| [Amazon Fire TV Stick 4K Plus](https://www.amazon.com/dp/B0CJM1GNFQ) | `Smart Camera` | smart home devices | Mild: a list of things the remote can control |
| [Amazon Fire TV Stick 4K Plus](https://www.amazon.com/dp/B0CJM1GNFQ) | `Fire TV Stick 4K Plus, Fire TV Stick` | other Fire TV Sticks | Real: a compatibility list naming other models |
| [Amazon Fire TV Stick 4K Plus](https://www.amazon.com/dp/B0CJM1GNFQ) | `To use Wi-Fi 6, you’ll need a compatible` | eero Pro 6 router | Real: names another product |
| [Amazon Fire TV Stick 4K Plus](https://www.amazon.com/dp/B0CJM1GNFQ) | `If you have a Wi-Fi 6E router, we` | Fire TV Stick 4K Max | Real: recommends a different model |
| [Lodge Pre-Seasoned Cast Iron Skillet 10.25 in](https://www.amazon.com/dp/B00006JSUA) | `Miniature Skillet` | none | Judge mistake: Amazon's "Model Name" field for this skillet |
| [Greenies Regular Dental Dog Treats, Original, 36 Count](https://www.amazon.com/dp/B006W6YHHI) | `Choose the Right Size for Your Dog: TEENIE` | other pack sizes | Mild: size chart for the product's own sizes |
| [KONG Classic Dog Toy](https://www.kongcompany.com/kong-classic/) | `PAIR W/ FOOD & TREATS` | food and treats sold separately | Real, mild: a tip to buy other things |
| [beyerdynamic DT 770 PRO X Limited Edition Studio Headphones](https://north-america.beyerdynamic.com/p/dt-770-pro-x-limited-edition) | `For decades, professionals have relied on the DT` | older DT 770 PRO | Real: about the older model |
| [Paula's Choice SKIN PERFECTING 2% BHA Liquid Exfoliant](https://www.paulaschoice.com/skin-perfecting-2pct-bha-liquid-exfoliant/201-2010.html) | `10-Count / 10-Count Pads` | the pads version | Mild: a size option on this page |
| [Paula's Choice SKIN PERFECTING 2% BHA Liquid Exfoliant](https://www.paulaschoice.com/skin-perfecting-2pct-bha-liquid-exfoliant/201-2010.html) | `AHA exfoliants are great for normal to dry` | AHA exfoliants | Real: about a different product type |
| [Revealer Concealer](https://kosas.com/products/revealer-concealer) | `100% Eye Cream.` | none | Judge mistake: the concealer's own tagline "100% Concealer. 100% Eye Cream." |
| [Cushioned Harness + Waterproof Leash Dog Walk Kit](https://wildone.com/products/harness-walk-kit) | `Experts recommend Collars for everyday wear, however, a` | a collar | Real, mild: harness vs collar advice |

## Part 2: what it missed

| # | Page | Sentences missed | What it is |
|---|---|---|---|
| 1 | [Sony WH-1000XM6 Wireless Noise Cancelling Headphones Black](https://www.bestbuy.com/product/sony-wh-1000xm6-best-wireless-noise-cancelling-headphones-black/J7XSRH5RCF) | 39 | The whole manufacturer feature section (noise cancelling, sound quality, 30-hour battery, controls, comfort) and the Q&A (mic mute, USB-C, foldable) |
| 2 | [Detox Clarifying Shampoo](https://theouai.com/products/detox-shampoo) | 26 | The product description, how and when to use it, the ingredient list, the FAQ, the sizes |
| 3 | [Always Pan](https://fromourplace.com/products/always-essential-cooking-pan) | 26 | FAQ answers (PFAS-free, 10-in-1, warranty, oven safe), the coating and material story, cooking features |
| 4 | [Sizzle Olive Oil](https://www.graza.co/products/sizzle) | 22 | The bottle features and the brand story (harvest timing, 13 lbs of olives a liter, never blended, everyday cooking uses) |
| 5 | [3-in-1 Foldable Qi2 Wireless Charging Stand](https://satechi.com/products/3-in-1-foldable-qi2-wireless-charging-stand) | 22 | FAQ answers (charging speeds, Apple Watch, StandBy), case rules, the compatible-phone lists, size and weight |
| 6 | [The Body Wash (Fragrance-Free)](https://necessaire.com/products/the-body-wash-pump-fragrance-free) | 20 | How to use, warnings, 4 FAQ answers (allergens, no SLS, pregnancy, eczema seal), the ingredient list, skin types |
| 7 | [Revealer Concealer](https://kosas.com/products/revealer-concealer) | 19 | The shade list, the full ingredient list, how to apply, the claims (brightening, hydrating) |
| 8 | [Boy Brow](https://www.glossier.com/products/boy-brow) | 18 | The full ingredient lists, what each key ingredient does, how to apply, the size |
| 9 | [beyerdynamic DT 770 PRO X Limited Edition Studio Headphones](https://north-america.beyerdynamic.com/p/dt-770-pro-x-limited-edition) | 18 | The technical data table (impedance, weight, ear pads) and FAQ answers (new driver, detachable cable, comfort) |
| 10 | [Cushioned Harness + Waterproof Leash Dog Walk Kit](https://wildone.com/products/harness-walk-kit) | 18 | Leash and harness sizing advice, the size table, what's in the kit, care instructions |
| 11 | [KONG Classic Dog Toy](https://www.kongcompany.com/kong-classic/) | 15 | The 6 size dimensions, the description, how to use it (stuff with kibble), safety notes, material |
| 12 | [Luxe Sateen Core Sheet Set](https://www.brooklinen.com/products/luxe-core-sheet-set) | 14 | The size guide, care instructions, the certification number, 2 short claims |
| 13 | [SOS Daily Rescue Facial Spray](https://www.tower28beauty.com/products/sos-daily-facial-rescue-spray) | 12 | FAQ answers (what hypochlorous acid does, not a setting spray, skin types, eczema seal, using with actives, when you see results) |
| 14 | [Paula's Choice SKIN PERFECTING 2% BHA Liquid Exfoliant](https://www.paulaschoice.com/skin-perfecting-2pct-bha-liquid-exfoliant/201-2010.html) | 12 | A few FAQ answers (is it a toner, sunscreen), the pH, the ingredient list in one line |
| 15 | [Men's Tree Runners](https://www.allbirds.com/products/mens-tree-runners) | 12 | Fit and materials, the size table, weight, heel drop, country of origin, a press quote |
| 16 | [Greenies Regular Dental Dog Treats, Original, 36 Count](https://www.amazon.com/dp/B006W6YHHI) | 8 | FAQ answers (vet recommended, how many a day, texture), barcodes |
| 17 | [Snap-on Phone Stand & Wallet (MagSafe Compatible)](https://www.moft.com/products/iphone-stand-wallet-magsafe-compatible) | 7 | FAQ answers (Apple Pay, angle on different iPhones), weight and thickness |
| 18 | [LMNT Recharge Electrolyte Drink Mix (Citrus Salt)](https://drinklmnt.com/products/lmnt-recharge-electrolyte-drink?variant=16358367199266) | 6 | The badges (vegan, paleo-keto, no dodgy ingredients) and the potassium and magnesium amounts |
| 19 | [Amazon Fire TV Stick 4K Plus](https://www.amazon.com/dp/B0CJM1GNFQ) | 5 | The sustainability section (carbon footprint) and one Alexa+ line |
| 20 | [Vintage French Terry Crew Neck Sweatshirt](https://mackweldon.com/products/vintage-french-terry-crew-neck-sweatshirt) | 5 | The description (French terry, raglan sleeves), material and care, number of colours |
| 21 | [Bose QuietComfort Ultra Headphones](https://www.bose.com/pxp/bose-quietcomfort-ultra-headphones) | 5 | The tagline and 3 sound-mode descriptions |
| 22 | [Stagg EKG Electric Kettle](https://fellowproducts.com/products/stagg-ekg-electric-pour-over-kettle) | 4 | Weight, the handle and display features, a menu walkthrough title |
| 23 | [ZWILLING Pro 8-inch Chef's Knife](https://www.zwilling.com/us/zwilling-pro-8-inch-chefs-knife-38401-203/38401-203-0.html) | 4 | The collection description (made in Germany, pinch grip) and the knife's uses |
| 24 | [Lemon Lime Hydration Multiplier](https://www.liquid-iv.com/products/lemon-lime-hydration-multiplier) | 3 | The product name, a serving line, "gluten free, non-GMO" |
| 25 | [Everyday Backpack](https://www.peakdesign.com/products/everyday-backpack) | 3 | 3 description sentences (camera bag and everyday bag in one) |
| 26 | [Kore Short Lined 7" - Black](https://vuoriclothing.com/products/kore-short-black) | 3 | Fit (7" inseam, liner) and features (pockets, drawcord) |
| 27 | [Levi's Men's 501 Original Fit Button Fly Stretch Jeans](https://www.macys.com/shop/product/levis-mens-501-original-fit-button-fly-stretch-jeans?ID=1376664) | 3 | The product name, Big & Tall, the material and care line |
| 28 | [Maybelline Lash Sensational Sky High Mascara, True Black](https://www.amazon.com/dp/B09VCXH9GF) | 3 | Amazon's specs and measurements tables and the long product title |
| 29 | [Lodge Pre-Seasoned Cast Iron Skillet 10.25 in](https://www.amazon.com/dp/B00006JSUA) | 3 | Material, capacity, the long product title (oven, stove, grill, campfire) |
| 30 | [Barbour Classic Bedale Waxed Jacket](https://www.barbour.com/us/classic-bedale%C2%AE-waxed-jacket-MWX0010OL7148.html) | 2 | The SKU and the material and care information |
| 31 | [Nalgene 32oz Wide Mouth Bottle](https://nalgene.com/product/32oz-wide-mouth-bottle/) | 2 | The colour and one description sentence (BPA-free, made from Tritan Renew) |
| 32 | [Rare Beauty by Selena Gomez Soft Pinch Liquid Blush](https://www.sephora.com/product/rare-beauty-by-selena-gomez-soft-pinch-liquid-blush-P97989778) | 2 | The shade and size line, the highlights (vegan, long-wearing) |
| 33 | [Stanley Quencher H2.0 FlowState Tumbler 40 oz, Charcoal](https://www.amazon.com/dp/B0BD78MYPN) | 1 | Amazon's one-line product summary |

Nothing missed: Slub Classic Tee.

### 1. Sony WH-1000XM6 Wireless Noise Cancelling Headphones Black: 39 missed

https://www.bestbuy.com/product/sony-wh-1000xm6-best-wireless-noise-cancelling-headphones-black/J7XSRH5RCF

Why: Best Buy's "From the Manufacturer" block is inside a frame the opener can't reach.

- Section *other places on the page*:
    - Authenticity, in every note.
    - Real-time File Restoration.
    - Ultra-clear calls, from anywhere.
    - Tailored Comfort, Thoughtfully Designed.
    - Intuitive Controls.
    - Long Battery Life and Convenient Charging.
- Section *Does it have mute (microphone) button?*:
    - Yes, the Sony WH-1000XM6 headphones include a built-in microphone mute feature.
    - You can mute or unmute the microphone by double-pressing the noise-canceling (NC) button on the headphones.
    - This functionality is particularly useful during calls or virtual meetings, allowing you to quickly control your microphone without needing to interact with your connected device.
    - To activate the headphones' microphone, launch the Sony Sound Connect app and go to the device settings.
    - Under the system section, locate the option labeled "Enable the headphones microphone" and switch it to "on." Once the microphone is enabled, you can conveniently turn it on or off by quickly pressing the NC/AMB (Noise Canceling/Ambient Sound Mode) button twice.
- Section *Adaptive NC Optimizer*:
    - Adaptive NC Optimizer.
    - Our new Adaptive NC Optimizer intelligently adjusts to a variety of factors, including external noise, air pressure, and your wearing style, even while wearing a hat or glasses, for uninterrupted, immersive sound.
    - This technology monitors your environment to ensure you enjoy the purest, most immersive sound, no matter where you are.
- Section *Auto Ambient Sound Mode*:
    - Auto Ambient Sound Mode.
    - Our Auto Ambient Sound mode adapts to your surroundings in real time by balancing music and external sound.
    - The multiple microphones can filter out the noise or let in what matters: announcements, conversations, or the world around you.
- Section *High-Resolution Audio*:
    - High-Resolution Audio.
    - The WH-1000XM6 headphones support High-Resolution Audio and High-Resolution Audio Wireless, thanks to LDAC, our industry-adopted audio coding technology.
    - LDAC transmits approximately three times more data than conventional Bluetooth audio for exceptional High-Resolution Audio quality.
- Section *Sound Connect app*:
    - Sound Connect app.
    - Customize your listening experience with the Sony | Sound Connect app.
    - Fine-tune noise cancellation, adjust ambient sound levels, and personalize your EQ settings for your ideal listening preferences.
- Section *Long Battery Life and Convenient Charging*:
    - With up to 30 hours of battery life, these headphones ensure you’re powered for even the longest trips.
    - When you’re running low, simply plug in the USB charging cable and keep listening, with both Bluetooth and audio cable connections supported.
    - Charge for 3 minutes and you'll get up to 3 hours of playback with an optional USB-PD compatible AC adapter.
- Section *Real-time File Restoration*:
    - Using Edge-AI, DSEE Extreme upscales compressed digital music files in real time.
    - It dynamically identifies instrumentation, musical genres, and individual elements of each song, restoring high-range sound lost during compression.
- Section *Tailored Comfort, Thoughtfully Designed*:
    - A wider, asymmetrical headband with smooth synthetic leather ensures a pressure-free, all-day fit, while a stepless slider and seamless swivel make every adjustment effortless.
    - Crafted for durability and style, the foldable design features precision metalwork and a compact case with a magnetic closure—ready to go wherever you do, in three premium colors: Black, Platinum Silver, Midnight Blue, Sand Pink, and Sandstone
- Section *Intuitive Controls*:
    - Tap, swipe, or press to switch between noise Cancelling, ambient sound, and mute, seamlessly.
    - Adjust your music and volume with a simple touch, and your headphones even pause when you start talking, so you stay connected without missing a beat.
- Section *HD Noise Cancelling Processor QN3*:
    - With more microphones than ever, we can precisely detect external noise and counteracts it with opposite soundwaves, delivering a new level of noise cancellation.
- Section *Authenticity, in every note*:
    - The newly developed look-ahead noise shaper D/A conversion technology in our HD Noise Cancelling Processor QN3, better anticipates and adjusts the combined output—reducing distortion, enhancing bass energy, and delivering crisp, fast sound that stays true to your music.
- Section *Ultra-clear calls, from anywhere*:
    - A six-microphone AI-based beamforming system, intelligent noise reduction technology, and wind-resistant design, work together to isolate your voice, filter out background noise, and ensure every word comes through crisp and clear—even in the busiest environments.
- Section *Does this headphone have active noise cancelling?*:
    - Yes, this headphone has active noise cancelling feature.
- Section *What kind of charging port does the headphone use?*:
    - The headphone uses a USB-C charging interface.
- Section *Are these headphones able to fold?*:
    - Yes, these headphones have a foldable design.
- Section *What is the battery life of these headphones?*:
    - The battery life of these headphones is up to 30 hours.

### 2. Detox Clarifying Shampoo: 26 missed

https://theouai.com/products/detox-shampoo

- Section *TELL ME MORE*:
    - Been on a dry shampoo binge?
    - This concentrated shampoo with apple cider vinegar will deeply cleanse away dirt, oil, and impurities.
    - It also removes buildup from styling products and hard water deposits.
    - Leaves hair feeling refreshed and super clean.
    - Yes, I am fully recyclable!
    - Good to Know Detox your OUAI by pairing it with your favorite hair shampoo 1-2x a week as a deep cleansing clarifying treatment or use in place of your main shampoo for more oily hair types as needed!
    - WHEN TO USE DETOX SHAMPOO: Use DETOX SHAMPOO when your hair and scalp feel like it needs a reset (if your scalp feels oily, hair is looking dull, curls have lost their shape & bounce).
    - Apply in place of or before using your favorite hair shampoo for the best cleanse.
    - FOR DRY HAIR TYPES: To prevent stripping your scalp from its natural oils or feeling too dry, we suggest starting off with 1 wash a week and gradually using more as needed.
    - FOR OILY HAIR TYPES: Detox a-OUAI by pairing it with your favorite hair shampoo 1-2x a week as a deep cleansing clarifying treatment or use in place of your main shampoo as needed!
- Section *How to use*:
    - •Use DETOX SHAMPOO whenever a deeper cleanse is needed.
    - •Thoroughly wet hair from roots to ends with lukewarm water.
    - Squeeze out excess moisture.
    - •Lather a quarter size amount in hands.
    - •Part hair using fingers and apply as close to the scalp as possible.
    - •Let sit for 1 to 3 minutes.
    - Squeeze out excess moisture before applying conditioner.
    - •Rinse with cold water to close the hair cuticle and add shine.
- Section *ingredients*:
    - Aqua (Water, Eau), Sodium C14-16 Olefin Sulfonate, Sodium Lauroyl Methyl Isethionate, Cocamidopropyl Betaine, Decyl Glucoside, Acrylates Copolymer, Cocamide Mipa, Parfum (Fragrance), Polysorbate 20, Hydrolyzed Keratin, Hydroxypropyl Guar Hydroxypropyltrimonium Chloride, Vinegar, Peg-150 Distearate,
    - Polyquaternium-7, Glycerin, Benzophenone-4, Sodium Chloride, Sodium Hydroxide, Tetrasodium Edta, Trisodium Ethylenediamine Disuccinate, Isopropyl Alcohol, Propylene Glycol, Citric Acid, Disodium Edta, Chlorphenesin, Sodium Benzoate, Phenoxyethanol, Potassium Sorbate, Linalool, Citronellol, Ci 14700
    - (Red 4), Ci 19140 (Yellow 5), Ci 61570 (Green 5) As always, all of our products are: Paraben-Free, Cruelty-Free, Gluten-Free, Phthalate-Free, SLS and SLES-Free [{"variant_id":"39726738407494" , "metafield_value":""},{"variant_id":"40213559214150" , "metafield_value":""}]
- Section *Q: Is it also good for men?*:
    - It lifts away product residue and buildup, deeply cleanses hair and scalp leaving hair refreshed.
    - It also balances the scalp and aids in natural detoxification process.
- Section *Payten*:
    - The chelating ingredients helps to remove deposits in hard water that can cause color fading and damage.
    - Generally we recommend usage one 1x a week, particularly if hair is feeling dry or brittle.
- Section *Size:*:
    - FULL SIZE (10 OZ) TRAVEL (3 OZ) JUMBO (16 OZ) REFILL (32 OZ)

### 3. Always Pan: 26 missed

https://fromourplace.com/products/always-essential-cooking-pan

- Section *FAQs*:
    - Our nonstick ceramic coating is made without potentially toxic materials like PFAS (including PTFEs and PFOAs), lead and cadmium (which are used in many other pots and pans).
    - It means we had 10 (!) different products and functions in mind when designing this pan, and we made sure the Ceramic Nonstick Always Pan could do all of them.
    - With this pan, you can seamlessly saute, fry, bake, roast, sear, boil, braise, strain, serve, and store!
    - For our Ceramic Nonstick Always Pan, we offer a 3 year limited warranty.
    - If you follow our easy care instructions, we’ll help out if anything goes wrong within 3 years from your date of purchase.
    - The Always Pan® is made with 100% certified post-consumer recycled aluminum.
    - Translation: The pan is made from discarded materials (not just factory floor scraps), and all wastewater is treated and recycled in the manufacturing process.
    - Plus, our packaging is made with biodegradable materials, innovatively designed to minimize waste.
    - So, for over two years, we listened and learned, taking all your feedback and incorporating technological advancements to create an even better version of the best pan there is.
    - From oven-safety to a longer-lasting nonstick ceramic coating to more sustainable manufacturing processes, we’re always improving.
    - Yes, our ceramic nonstick Always Pan is oven-safe up to 450°F.
- Section *other places on the page*:
    - Accessories: Included.
    - Note the Nonstick.
    - That’s So Deep.
- Section *Non-toxic Nonstick Coating*:
    - A nonstick like no other, our 50% longer-lasting Thermakind® ceramic coating is exclusive to Our Place and the only nonstick you’ll want for your place.
    - Mainly comprised of sand derivative, water, and alcohol, you won’t find harmful chemicals hiding in the nonstick coating.
- Section *Lightweight Aluminum Body*:
    - The ultra-conductive Ceramic Nonstick Always Pan body and lid is made from sturdy aluminum, which is three times more heat conductive than stainless steel (did you know that?).
    - Fast, easy cooking is just a matter of turning on your stove.
- Section *Oven Safe up to 450°F*:
    - Unlock a whole new cache of recipes with stovetop-to-oven abilities.
    - Whether you’re finishing off crispy chicken thighs or making an impressive dutch baby, you’ll find yourself reaching for your Ceramic Nonstick Always Pan well…always!
- Section *$135*:
    - Size options
- Section *Color:*:
    - Spice Spice Char Blue Salt Sage Steam
- Section *Third-Party Tested for Safety*:
    - Lab-verified by Light Labs May 2026 692 substances screened See the report 3-Year Warranty 100-Day Trial Free Shipping & Returns
- Section *Accessories: Included*:
    - Say goodbye to messy counters and crowded spoon rests thanks to the included Beechwood Spatula that nests securely across the pan’s long handle.
- Section *Note the Nonstick*:
    - The exclusive, non-toxic Thermakind® nonstick coating is super slippery, making it a dream for eggs, starches, sautes, and more.
- Section *That’s So Deep*:
    - Deeper than your average fry pan, the Ceramic Nonstick Always Pan depth and handy, generous pour spouts make it that much more versatile.

### 4. Sizzle Olive Oil: 22 missed

https://www.graza.co/products/sizzle

Why: The text is visible on the screenshot; the model just didn't copy most of it.

- Section *“Sizzle”*:
    - Protects our light-sensitive oil!
    - For an easy, controlled pour!
    - Screw-on cap keeps oxygen out!
    - Extra Virgin Olive Oil Buy Now “Sizzle” is 100% Extra Virgin cooking oil .
    - Cooking Oil Harvested in November Olives are picked during peak harvest season when they are more mature and a bit juicier.
    - 13LBS OF OLIVES = 1 LITER OF OIL When pressed, these more mature, juicier olives yield a lot more oil.
    - It only takes about 13 lbs of Picual olives harvested in December/January to produce the same liter of cooking oil.
    - TAKES IT EASY Just like (most) humans, olives get more chill with age.
    - The oil produced from these olives will be milder in flavor, and make for a more flexible cooking oil.
    - #1 COOKING OIL Because of its mellow flavor and higher smoke point, Sizzle can be used in a lot of different ways.
    - Great for everyday cooking like roasting, searing, poaching, pan frying, baking, and marinating.
- Section *5 Stars*:
    - Most olive oil you see on shelves was over a year old before it was even bottled.
    - Graza is picked, pressed, bottled, and shipped all in the same season.
    - And not to keep bragging, but we put the harvest date right on the label.
    - Does your other olive oil do that?
    - “Made in Italy” doesn’t mean what you think.
    - Major olive oil brands save money by blending low-quality, old oils (some olive, some not) from all over the globe while slapping a country name on the label.
    - Graza is never blended, and it always comes from one place.
    - \Real\ extra virgin oil is made from olives that are harvested early (fall or winter) and pressed ASAP so the oil stays fresh, delicious, and full of polyphenols (that healthy stuff that makes you live a long time).
    - Most bottles labeled “extra virgin” aren’t the real deal, but Graza is and always will be.
- Section *Extra Virgin Olive Oil*:
    - Select Product: Squeeze Squeeze 750ml Glass 750ml
- Section *Fun things to do with “Sizzle”*:
    - Sizzle some Runny Eggs

### 5. 3-in-1 Foldable Qi2 Wireless Charging Stand: 22 missed

https://satechi.com/products/3-in-1-foldable-qi2-wireless-charging-stand

- Section *FAQ*:
    - The 3-in-1 Foldable Qi2 Wireless Charging Stand operates at the following frequencies: Wireless 1: 15W for wireless load at 360 kHz (MPP).
    - Wireless 2and 3: 5W for AirPods and Apple Watch at 326.5 kHz.
    - The stand will only support MagSafe-enabled devices.
    - If there is a MagSafe compatible case or adapter for these legacy devices, then it should allow the device to wirelessly charge with our stand.
    - Yes, the charger supports fast charging to Apple Watch Series 7 -10.
    - Apple Watches Series 1-6 and SE models are still supported and will charge at a normal charging rate.
    - Apple Watch Ultra and Ultra 2 will charge at up to 5W max.
    - As long as the setting on your iOS device has StandBy Mode enabled, simply lock your phone and mount it to the magnetic Qi2 wireless charger, turn your phone sideways horizontally, and the display should switch.
    - There are multiple settings and widgets to choose from to best suit your needs.
- Section *Disclaimers*:
    - Compatible Systems: Supports Qi-, Qi2-, and MagSafe wireless charging.
    - Cases on host devices must be no thicker than 3mm and are free of any metal housing or plating to not interfere with the wireless charging.
    - If using a case it must be Qi2 / MagSafe compatible for optimal use.
    - *Flat-level surface charging only; Qi2/MagSafe-compatible adapter or case is required for the magnetic feature.
- Section *iPhone*:
    - iPhone 18 Pro Max (2026) iPhone 18 Pro (2026) iPhone Air (2025) iPhone 17 Pro Max (2025) iPhone 17 Pro (2025) iPhone 17 (2025) iPhone 16e (2025) iPhone 16 Pro Max (2024) iPhone 16 Pro (2024) iPhone 16 Plus (2024) iPhone 16 (2024) iPhone 15 Pro Max (2023) iPhone 15 Pro (2023) iPhone 15 Plus (2023)
    - iPhone 15 (2023) iPhone 14 Pro Max (2022) iPhone 14 Pro (2022) iPhone 14 Plus (2022) iPhone 14 (2022) iPhone 13 Pro Max (2021) iPhone 13 Pro (2021) iPhone 13 (2021) iPhone 13 Mini (2021) iPhone 12 Pro Max (2020) iPhone 12 Pro (2020) iPhone 12 (2020) iPhone 12 Mini (2020) iPhone 17e iPhone 11 Series
- Section *Regular price*:
    - Unit price / per Title Default Title Delivers 15W iPhone, 5W AirPods, and 5W Apple Watch charging Powered by Qi2 technology for efficient, reliable wireless power Foldable design supports portrait and landscape viewing modes Premium aluminum build with vegan leather base and durable hinges LEARN
- Section *Dimensions & Weight*:
    - Length: 3.5 in / 9 cm Width: 3.5 in / 9 cm Height: 6.22 in / 15.8 cm Weight: 10.5 oz / 303.5 g
- Section *Model # / UPC*:
    - ST-Q31FM-EA / 810086361175
- Section *Speed*:
    - Qi2 Wireless Charger - up to 15W AirPods - 5W Apple Watch Charger - fast charging up to 5W *Fast charging is only supported with Apple Watch Series 7/8/9/Ultra/Ultra 2 Compatibility
- Section *Google*:
    - Pixel 9 Series* Pixel Fold* Pixel 8 Series* Pixel 7 Series*
- Section *OnePlus*:
    - OnePlus 12* OnePlus 10 Pro* OnePlus 9 / Pro* OnePlus 8 Pro*
- Section *Sony*:
    - Xperia 1 (II - VI)* Important Info

### 6. The Body Wash (Fragrance-Free): 20 missed

https://necessaire.com/products/the-body-wash-pump-fragrance-free

Why: The improved runs took a shorter screenshot of this page (6 slices, against 8 in an earlier run), so the FAQ at the bottom wasn't in it.

- Section *Usage*:
    - Apply all-over body.
    - Massage for rich foam.
    - Warning: Use as directed only.
    - If product enters eye, rinse with water.
    - If irritation occurs, discontinue use, and consult a physician.
- Section *Is This Product Certified Gluten-Free, Soy-Free And Nut-Free*:
    - This product is not certified gluten-free, soy-free or nut-free.
    - This product is manufactured in a facility where gluten, soy and nuts may be used.
    - If you have an allergy to gluten, soy or nuts, we recommend that you do not use this product.
    - If you still wish to try, please consider a patch test.
- Section *Is The Body Wash Made Wtihout SLS/SLES?*:
    - The Body Wash is made without SLS/SLES.
    - The Body Wash uses gentle plant surfactants for a non-stripping, non-drying experience.
    - The Body Wash is also made without Silicones, Phthalates, Parabens and PEGs.
- Section *Can I Use The Product If I Am Pregnant?*:
    - This formula is Dermatologist-Tested and Hypoallergenic.
    - If you have any specific concerns, we recommend consulting your doctor prior to use.
- Section *Full Ingredients*:
    - Aqua/Water/Eau Sodium Laurylglucosides Hydroxypropylsulfonate Caprylyl/Capryl Glucoside Disodium Cocoamphodiacetate Glycerin Sodium Chloride Sodium Methyl Cocoyl Taurate Niacinamide Ceramide AG Ceramide AP Ceramide EOP Ceramide NG Ceramide NP Sodium Hyaluronate Linoleic Acid Linolenic Acid Centella
    - Asiatica Extract Tocopherol Sclerocarya Birrea Seed Oil Glyceryl Stearate Oleic Acid Palmitic Acid Stearic Acid Sucrose Distearate 1,2-Hexanediol Cholesterol Dipropylene Glycol Hydrogenated Lecithin Caprylyl Glycol Phenoxyethanol Citric Acid Chlorphenesin Sodium Phytate Ingredients are subject to
- Section *Is This Product Certified Eczema-Safe?*:
    - This product has undergone the specific safety testing protocol required to obtain this seal.
- Section *TARGETS*:
    - Dry Skin Sensitive Skin
- Section *TREATMENT*:
    - Formula Contains: Plant Surfactants Glycerin Niacinamide Ceramide AG Ceramide AP Ceramide EOP Ceramide NG Ceramide NP Hyaluronic Acid Omega-6 Omega-9 Centella Asiatica Extract See Ingredients Close
- Section *TESTED*:
    - Product Is: Dermatologist-Tested Hypoallergenic Non-Comedogenic EU Compliant Certified Vegan Cruelty-Free

### 7. Revealer Concealer: 19 missed

https://kosas.com/products/revealer-concealer

- Section *The Shades*:
    - Skin Tone Light Light Medium Deep Swatches Tone 0.5 N Very light with neutral undertones Selected Tone 0.7 C Very light with cool pink undertones Selected Tone 01 N Very light with neutral peach undertones Selected Tone 1.5 C Light with cool pink undertones Selected Tone 02 W Light with warm golden
    - undertones Selected Tone 2.3 N Light with neutral undertones Selected Tone 2.5 C Light with cool peach undertones Selected Tone 2.6 C Light with cool pink undertones Selected Tone 03 W Light with warm golden undertones Selected Tone 3.2 O Light+ with olive undertones Selected Tone 3.5 W Light+ with
    - warm peach undertones Selected Tone 3.6 C Light+ with cool pink undertones Selected Tone 3.8 N Light medium with neutral peach undertones Selected Tone 04 N Light medium with neutral golden undertones Selected Tone 4.5 N Light medium with neutral pink undertones Selected Tone 05 W Medium with warm
    - golden undertones Selected Tone 5.3 C Medium with cool peach undertones Selected Tone 5.5 O Medium with olive undertones Selected Tone 5.8 N Medium with neutral undertones Selected Tone 06 O Medium Tan with olive undertones Selected Tone 6.2 N Medium Tan with neutral peach undertones Selected Tone
    - Medium Tan with neutral pink undertones Selected Tone 6.5 O Medium Tan with neutral olive undertones Selected Tone 6.8 W Medium Tan with warm peach undertones Selected Tone 07 N Medium Deep with neutral golden undertones Selected Tone 7.3 N Medium Deep with neutral peach undertones Selected Tone
    - 7.5 W Medium Deep with warm peach undertones Selected Tone 7.8 N Medium Deep with neutral undertones Selected Tone 08 W Medium Deep with warm golden undertones Selected Tone 8.1 O Medium Deep with olive undertones Selected Tone 8.2 W Deep with warm golden undertones Selected Tone 8.5 C Deep with
    - cool peach undertones Selected Tone 8.7 N Deep with neutral golden undertones Selected Tone 8.8 N Deep with neutral olive undertones Selected Tone 9.1 N Deep with neutral undertones Selected Tone 9.5 N Deep with neutral red undertones Selected Tone 10 W Rich deep with warm golden undertones
    - Selected Tone 10.5 N Rich deep with neutral undertones Selected
- Section *BENEFITS & INGREDIENTS*:
    - Caffeine + Pink Algae Brightens Full Ingredients Hyaluronic Acid Hydrates Full Ingredients Arnica Calms Full Ingredients Peptides Visibly Smoothes Fine Lines & Wrinkles Full Ingredients Aqua/Water/Eau, Caprylic/Capric Triglyceride, Mica, Octyldodecanol, Polyglyceryl-2 Dipolyhydroxystearate,
    - Ethylhexyl Olivate, Undecane, Glycerin, Polyhydroxystearic Acid, Polyglyceryl-3 Diisostearate, Galactoarabinan, Helianthus Annuus (Sunflower) Seed Cera (Wax), Tridecane, Propanediol, Lecithin, Glyceryl Oleate, Pentylene Glycol, Panthenol, Sodium Hyaluronate, Palmitoyl Tripeptide-5, Caffeine,
    - Squalane, Dunaliella Salina (Algae) Extract, Tocopherol, Maltodextrin, Arnica Montana (Arnica) Flower Extract, Helianthus Annuus (Sunflower) Seed Oil, Rosmarinus Officinalis (Rosemary) Leaf Oil, Foeniculum Vulgare (Fennel) Fruit Oil, Potassium Sorbate, Sodium Benzoate, Glycine Soja (Soybean) Oil,
    - Phenethyl Alcohol, Ascorbyl Palmitate, Hydrogenated Palm Glycerides Citrate, Phytosterols [May Contain: Titanium Dioxide (CI 77891), Iron Oxides (CI 77491), Iron Oxides (CI 77492), Iron Oxides (CI 77499)] Size: .20 oz / 6 ml
- Section *Tone 0.5 N*:
    - Very light with neutral undertones Find Your Shade Filter by Shade Family & Undertone Shade Family: All shade families Very Light Light Light Medium Medium Medium Deep Deep Undertone: All undertones Cool Neutral Warm Olive Shade Tone 0.5 N Tone 0.7 C Tone 01 N Tone 1.5 C Tone 02 W Tone 2.3 N Tone
    - 2.5 C Tone 2.6 C Tone 03 W Tone 3.2 O Tone 3.5 W Tone 3.6 C Tone 3.8 N Tone 04 N Tone 4.5 N Tone 05 W Tone 5.3 C Tone 5.5 O Tone 5.8 N Tone 06 O Tone 6.2 N Tone 6.3 N Tone 6.5 O Tone 6.8 W Tone 07 N Tone 7.3 N Tone 7.5 W Tone 7.8 N Tone 08 W Tone 8.1 O Tone 8.2 W Tone 8.5 C Tone 8.7 N Tone 8.8 N
    - Tone 6.5 O Tone 6.8 W Tone 07 N Tone 7.3 N Tone 7.5 W Tone 7.8 N Tone 08 W Tone 8.1 O Tone 8.2 W Tone 8.5 C Tone 8.7 N - Sold Out Tone 8.8 N Tone 9.1 N - Sold Out Tone 9.5 N - Sold Out Tone 10 W - Sold Out Tone 10.5 N - Sold Out
- Section *What's the best way to apply Revealer Concealer?*:
    - The precision tip reaches tiny corners around the eyes, while the larger end blends quickly and seamlessly.
    - Fingers also work well for a natural finish.
- Section *Revealer Concealer*:
    - 2258 2258 total customer reviews Super Creamy + Brightening Concealer Size .18 oz / 5.3ml Full Size Mini
- Section *100% Concealer. 100% Eye Cream.*:
    - brightening hydrating safe for sensitive skin silicone free

### 8. Boy Brow: 18 missed

https://www.glossier.com/products/boy-brow

Why: Glossier's per-ingredient "+" rows have no markings the opener recognises.

- Section *Boy Brow ingredients*:
    - Close CLEAR INGREDIENTS Water/Aqua/Eau, Butylene Glycol, Beeswax/Cera Alba/Cire d’Abeille, Copernicia Cerifera (Carnauba) Wax/Cera Carnauba/Cire de Carnauba, Propylene Glycol Stearate, Glyceryl Stearate, Stearic Acid, Isostearic Acid, Dimethicone, PVP, Tromethamine, Oleic Acid, Lecithin, Soluble
    - Collagen, Sodium Hyaluronate, Silica, Polybutene, Cellulose Gum, Xanthan Gum, Glyceryl Laurate, Sorbitan Laurate, Isopropyl Titanium Triisostearate, Propylene Glycol Laurate, Polymethyl Methacrylate, Magnesium Aluminum Silicate, Polysorbate 20, Sodium Dehydroacetate, Caprylyl Glycol, Hexylene
    - Glycol, Phenoxyethanol, Disodium EDTA.
    - BLACK + BROWN + BLONDE + AUBURN INGREDIENTS Water/Aqua/Eau, Beeswax/Cera Alba/Cire d’Abeille, Butylene Glycol, Copernicia Cerifera (Carnauba) Wax/Cera Carnauba/Cire de Carnauba, Propylene Glycol Stearate, Glyceryl Stearate, Stearic Acid, Isostearic Acid, PVP, Mica, Tromethamine, Dimethicone, Oleic
    - Caprylyl Glycol, Hexylene Glycol, Phenoxyethanol, Disodium EDTA, Iron Oxides (CI 77499).
    - (±) May Contain/Peut Contenir: Titanium Dioxide (CI 77891), Iron Oxides (CI 77491, CI 77499), Ferric Ferrocyanide (CI 77510).
    - Hexylene Glycol, Phenoxyethanol, Disodium EDTA, Iron Oxides (CI 77491, CI 77492, CI 77499).
- Section *Ingredients that nourish and groom brows from root to tip.*:
    - Beeswax and Carnauba Wax Natural waxes that hold hairs in place without stiffness.
    - Oleic Acid An emollient found in olive oil that nourishes and moisturizes.
    - Lecithin Adds a silky, smooth texture and a subtle sheen.
    - Hyaluronic Acid A humectant which adds hydration and improves softness and flexibility to brow hairs.
- Section *In an unofficial study (aka we asked some friends), Boy Brow*:
    - We like to think of Boy Brow as an every-day essential for all brows, no matter the size, thickness, or look you’re going for.
    - Starting at the arch, remove the spoolie from the tube and brush brow hairs in an upward motion.
    - If needed, dip back in and apply to the tail.
    - Repeat on your other brow, unless that’s the look you’re going for.
- Section *other places on the page*:
    - In an unofficial study (aka we asked some friends), Boy Brow took an average of 6.3 seconds to apply—and looks amazing..
    - Boy Brow ingredients.
- Section *Boy Brow*:
    - Show More 0.13 oz / 3.8 g

### 9. beyerdynamic DT 770 PRO X Limited Edition Studio Headphones: 18 missed

https://north-america.beyerdynamic.com/p/dt-770-pro-x-limited-edition

- Section *TECHNICAL DATA*:
    - Wearing Style Over-ear
    - Operating principle Closed
    - Transmission Type Wired
    - Nominal Headband pressure Firm fit for professional use, 5.5 N
    - Earpad material Velours
    - Remote Without Remote
    - Nominal impedance headphones 48 ohms
    - Weight headphones without cable 305 g
    - EAN 4010118001543
- Section *How does the DT 770 PRO X differ from the DT 770 PRO?*:
    - The high product quality is achieved through manufacturing in Germany.
    - The DT 770 PRO X, on the other hand, uses the modern STELLAR.45 driver with 48 Ohms, which covers a wide frequency range of 5 – 40,000 Hz and can be operated on a variety of playback devices without compromising sound quality.
    - The DT 770 PRO X, however, features a detachable 3-meter cable with a lockable mini-XLR connector.
    - Additionally, the DT 770 PRO X has improved wearing comfort.
    - An integrated fontanelle recess in the headband reduces pressure on sensitive areas at the top of the head.
- Section *How does the DT 770 PRO X differ from the DT 700 PRO X?*:
    - The DT 770 PRO X features the well-known sound tuning of the DT 770 PRO with an emphasis on bass and treble.
    - The DT 770 PRO X comes with a detachable 3-meter cable.
    - All mentioned cables have a lockable mini-XLR connector for secure hold in the studio.
- Section *THE SOUND SIGNATURE OF A LEGEND*:
    - High wearing comfort thanks to fontanelle recess Detachable cable with mini-XLR connector STELLAR.45 driver system – powerful and precise Soft, breathable velour ear pads Closed-back design for high sound isolation

### 10. Cushioned Harness + Waterproof Leash Dog Walk Kit: 18 missed

https://wildone.com/products/harness-walk-kit

- Section *Looking for your perfect leash?*:
    - For tiny pups (20lbs and under), you'll love the Small Leash for daily strolling.
    - Larger dogs should size up, opting for our Standard Leash, which can handle heavy lifting.
    - No matter the size, customize leash lengths by unhooking the handle and reconnecting it to the lower D-ring for a shorter option that keeps pups close.
- Section *Chest*:
    - Extra Small 9" - 10" 12" - 15" Small 11" - 13" 15" - 21" Medium 14" - 17" 19" - 27" Large 16" - 22" 25" - 38"
    - Extra Small 22.9cm - 25.4cm 30.5cm - 38.1cm Small 27.9cm - 33cm 38.1cm - 53.3cm Medium 35.6xm - 43.2cm 48.3cm - 68.6xm Large 40.6cm - 55.9cm 63.5cm - 96.5cm
- Section *Sizing Tips*:
    - For growing puppies or dogs with extra thick coats, we recommend adding a little extra space (3 fingers) when measuring.
    - Our recommendation is to size up!
- Section *Meet the Wild One Harnesses*:
    - Cushioned Dog Harness.
- Section *Cushioned Dog Harness*:
    - Black / S Black / M Black / L Navy / XS Navy / S Navy / M Navy / L Moonstone / XS Moonstone / S Moonstone / M Moonstone / L Spruce / XS Spruce / S Spruce / M Spruce / L
- Section *Meet the Wild One Leashes*:
    - Waterproof Dog Leash.
- Section *Waterproof Dog Leash*:
    - Standard Black / Small Seafoam / Standard Seafoam / Small Lunar / Standard Lunar / Small Moonstone / Standard Moonstone / Small
- Section *other places on the page*:
    - (continued) Poop Bag Carrier.
- Section *Poop Bag Carrier*:
    - Diameter: 1.75" Height: 3.5" Care & Caution Cleaning Instructions Harness: Hand wash using mild soap and warm water (recommended method).
- Section *Safe & Secure*:
    - Durable quick-snap buckles on the back and neck (Seafoam, Blaze, Moss, Orchid, Lilac, and Black only) make it easy to get on and off, while ensuring security during walks.
- Section *Medium*:
    - 35.6cm - 43.2cm 48.3cm - 68.6cm
- Section **In between sizes? Our recommendation is to size up.*:
    - Extra Small Small Medium Large
- Section *Small*:
    - 1.27cm 1.3m 0.914m Up to 30 lbs
- Section *Standard*:
    - 1.91cm 1.68m 0.991m Up to 80 lbs

### 11. KONG Classic Dog Toy: 15 missed

https://www.kongcompany.com/kong-classic/

- Section *Dimensions:*:
    - 1.5 × 1.5 × 2.25 in.
    - 2.75 × 2.75 × 4 in.
    - 2.25 × 2.25 × 3.5 in.
    - 3.5 × 3.5 × 5 in.
    - 1.75 × 1.75 × 3 in.
    - 3.88 × 3.88 × 6 in.
- Section *FREQUENTLY BOUGHT TOGETHER*:
    - rubber The KONG Classic is the gold standard of dog toys and has become the staple for dogs around the world for over forty years.
    - Offering enrichment by helping satisfy dogs’ instinctual needs, the KONG Classic’s unique natural red rubber formula is ultra-durable with an erratic bounce that is ideal for dogs that like to chew while also fulfilling a dog’s need to play.
    - Be sure to stuff with tempting bits of kibble and entice with a dash of peanut butter.
- Section *Warning:*:
    - Supervised use only.
    - Remove all packaging and packaging attachments.
    - Discontinue use if damaged.
- Section *Toy Features:*:
    - rubber Made in the USA.
    - Globally Sourced Materials.
- Section *Materials:*:
    - 100% Rubber (Natural)

### 12. Luxe Sateen Core Sheet Set: 14 missed

https://www.brooklinen.com/products/luxe-core-sheet-set

- Section *Size Guide*:
    - 39” x 75” x 16”
    - 39” x 80” x 16”
    - 54” x 75” x 16”
    - 60” x 80” x 16”
    - 76” x 80” x 16”
    - 72” x 84” x 16”
    - Luxurious feel.
    - Our #1 fan-favorite.
- Section *Care*:
    - Our Luxe Sateen sheets get softer with every wash.
    - Machine wash cold with like colors.
    - Tumble dry low, remove promptly.
    - Non-Chlorine bleach only.
    - Warm iron if needed.
- Section *OEKO-TEX® STANDARD 100*:
    - Certification number: 17.HUS.08214.Hohenstein.

### 13. SOS Daily Rescue Facial Spray: 12 missed

https://www.tower28beauty.com/products/sos-daily-facial-rescue-spray

- Section *What does Hypochlorous Acid do in skincare?*:
    - Well, your body has —like hyaluronic acid and ceramides, *hypochlorous acid* is made naturally by your skin.
    - It's anti-inflammatory and antibacterial properties help defend skin from harmful bacteria, reduce redness, and soothe irritation.
- Section *What's different about Tower 28's Hypochlorous Acid?*:
    - Hypochlorous acid is normally an incredibly fickle ingredient and historically unstable, much like many forms of vitamin C.
    - Our chemists worked on stabilizing hypochlorous acid for 8 years before we launched SOS Daily Rescue Facial Spray, not to mention testing concentration and pH until we felt it was optimized for facial skin.
- Section *Is SOS Spray a makeup setting spray?*:
    - SOS Spray is not designed to prolong the wear of makeup.
    - It is a skincare toner treatment designed to soothe, purify, and strengthen the skin barrier.
- Section *What skin type is SOS Spray suitable for?*:
    - SOS Spray is excellent for skin maintenance in all skin types, but the most visible changes will be seen after consistent use in sensitive/problem skin types.
    - SOS Spray has the National Eczema Association's Seal of Acceptance™ —making it safe for even the most reactive and sensitive skin types.
- Section *Can I use SOS Spray with skincare actives like vitamin C?*:
    - If you're using an active like vitamin C as part of your skincare routine, we recommend using SOS Spray BEFORE any actives (allowing it to dry down fully first).
    - The hero ingredient in SOS Spray (hypochlorous acid) is super fast acting and once it dries down it won't interact with any of your other skincare products (including actives like vitamin C!)
- Section *🦞*:
    - Reduces Redness ✨ Clears Skin
- Section *How long do I have to use SOS Spray before I see results?*:
    - Some see results in 3 days, others 3 months, but consistency is key.

### 14. Paula's Choice SKIN PERFECTING 2% BHA Liquid Exfoliant: 12 missed

https://www.paulaschoice.com/skin-perfecting-2pct-bha-liquid-exfoliant/201-2010.html

- Section *Is 2% BHA Liquid Exfoliant a toner?*:
    - Our 2% BHA Liquid Exfoliant, commonly known as an exfoliating toner, has the lightweight, liquid texture of a toner and the benefits of a chemical exfoliant, which include removing dead skin cells and unclogging pores, revealing a brighter complexion.
    - Other Paula’s Choice toners are used after cleansing to remove residual makeup and sunscreen, hydrate, and deliver targeted ingredients like antioxidants.
    - While both are beneficial, they serve different purposes in your skincare routine.
- Section *Exfoliate*:
    - SKIN PERFECTING 2% BHA Liquid Exfoliant:.
    - Exfoliants balance skin and shed built-up layers, enhancing skincare effectiveness.
- Section *SKIN PERFECTING 2% BHA Liquid Exfoliant:*:
    - Most potent, deep exfoliation.
    - pH 3.5, for fast results.
- Section *Key Ingredients*:
    - Methylpropanediol Green Tea BHA (Beta Hydroxy Acid)
- Section *All Ingredients*:
    - Water Methylpropanediol Butylene Glycol Salicylic Acid Polysorbate 20 Camellia Oleifera Sodium Hydroxide Tetrasodium EDTA Paula's Choice is dedicated to maintaining the accuracy of the ingredient lists on this website.
- Section *Who should use a BHA exfoliant?*:
    - Its oil-soluble nature clears pores and improves texture.
- Section *At what age can you start using 2% BHA?*:
    - Regardless of age, daily sunscreen is essential due to increased sun sensitivity.
- Section *What is the difference between SKIN PERFECTING 2% BHA Liquid*:
    - Paula’s Choice currently offers four liquid BHA exfoliants including:

### 15. Men's Tree Runners: 12 missed

https://www.allbirds.com/products/mens-tree-runners

- Section *Men's Tree Runner*:
    - A light, breathable upper hugs your foot, while a Merino wool-blend lining provides all-day comfort—socks optional.
    - versatile, do-it-all pick for wearing just about anywhere.
    - FIT & FEEL Lightweight + Flexible.
    - BEST FOR Travelling Walking Commuting Everyday-ing UPPER Breezy tree knit made from eucalyptus fiber.
    - MIDSOLE & OUTSOLE Contoured, low-profile sugarcane-based SweetFoam® cushioning keeps you grounded.
    - Average Width Average Length US 8 9 10 11 12 13 14 UK 7 8 9 10 11 12 13 cm 26 27 28 29 30 31 32 Wear All Day Comfort Lightweight, bouncy, and wildly comfortable, Allbirds shoes make any outing feel effortless.
- Section *other places on the page*:
    - Weight: 8.6oz (M9), 7.3oz (W7).
    - Stack Height: Heel: 19.6mm Toe: 10mm.
    - Heel/Toe Drop: 9.0mm.
    - Country of Origin: Vietnam.
- Section *Best for*:
    - Traveling Walking Commuting Everyday View technical details
- Section *RESPONSIBLY SOURCED*:
    - “Almost immediately after slipping on the Allbirds Tree Runners, I was hooked—so much so that I wore them every day for the rest of the trip.” Condé Nast Traveler

### 16. Greenies Regular Dental Dog Treats, Original, 36 Count: 8 missed

https://www.amazon.com/dp/B006W6YHHI

- Section *Are Greenies Treats for Dogs recommended by veterinarians?*:
    - They are vet recommended for dental care and accepted by the Veterinary Oral Health Council (VOHC) to help control plaque and tartar.
- Section *Greenies Regular Dental Dog Treats, Original Chicken Flavor,*:
    - Veterinarian-recommended, supports oral health & freshens breath, clean to gumline, for dogs 25-50 lbs, chicken flavor Visit the Greenies Store 4.8 4.8 out of 5 stars (36,596) Amazon's Choice highlights highly rated, well-priced products available to ship immediately.
- Section *How should I feed my adult dog Greenies Dental Treats?*:
    - Feed your adult dog 1 Greenies Dental Treats of any flavor daily.
- Section *What is the texture of Greenies Dental Treats?*:
    - Greenies Dental Treats feature a delightfully chewy texture that fights plaque and tartar.
- Section *Additional Features*:
    - Odor Resistant
- Section *40K+ bought in past month*:
    - Size: 36 Count (Pack of 1) Make a Size selection
- Section *UPC*:
    - 642863101045
- Section *Global Trade Identification Number*:
    - 00642863101045

### 17. Snap-on Phone Stand & Wallet (MagSafe Compatible): 7 missed

https://www.moft.com/products/iphone-stand-wallet-magsafe-compatible

- Section *other places on the page*:
    - Does it affect Apple Pay usage?Apple Pay works normally..
    - Invisible Wallet Stand AERA.
    - Carrying Capacity.
- Section *Does it affect Apple Pay usage?Apple Pay works normally.*:
    - Will the angle on different iPhone 12/13 models change when propped up?
    - The angle varies on different iPhone 12/13 models due to the different sizes of the phones.
- Section *Size*:
    - 1.6oz weight/0.23in thick
- Section *Charging*:
    - ChargingWireless charging (one charge for 6 months use)

### 18. LMNT Recharge Electrolyte Drink Mix (Citrus Salt): 6 missed

https://drinklmnt.com/products/lmnt-recharge-electrolyte-drink?variant=16358367199266

- Section *other places on the page*:
    - NO DODGYINGREDIENTS.
    - VEGANFRIENDLY.
    - PALEO-KETOFRIENDLY.
- Section *FAQs*:
    - 200 mg POTASSIUM
    - 60 mg MAGNESIUM
- Section *PALEO-KETOFRIENDLY*:
    - PALEO-KETO FRIENDLY

### 19. Amazon Fire TV Stick 4K Plus: 5 missed

https://www.amazon.com/dp/B0CJM1GNFQ

- Section *Sustainability features*:
    - This product has sustainability features recognized by trusted certifications.
- Section *Carbon impact*:
    - Carbon emissions from the lifecycle of this product were reduced compared to similar products or previous models.
- Section *Designed for Sustainability*:
    - See Fire TV Stick 4K Plus fact sheet
- Section *Carbon Footprint*:
    - 33kg CO 2 e total carbon emissions
- Section *This device is designed for Alexa+*:
    - Enjoy an advanced Alexa+ experience across all compatible devices with Prime.

### 20. Vintage French Terry Crew Neck Sweatshirt: 5 missed

https://mackweldon.com/products/vintage-french-terry-crew-neck-sweatshirt

- Section *Product Details*:
    - No frills, all comfort.
    - We set out to recreate the feeling of a classic crew—albeit with a polished look and 100% premium French terry.
    - Featuring vintage-inspired stitching and raglan sleeves for greater freedom of movement, this is bound to become your new (old) favorite.
- Section *Vintage French Terry Crew Neck Sweatshirt*:
    - 4 colors available
- Section *Material + Care*:
    - 100% Cotton Machine wash cold.

### 21. Bose QuietComfort Ultra Headphones: 5 missed

https://www.bose.com/pxp/bose-quietcomfort-ultra-headphones

- Section *other places on the page*:
    - Push the boundary of listening
    - Mind-bending natural sound.
- Section *Mind-bending natural sound*:
    - What you're listening to sounds so real it's almost like you can reach out and touch it.
- Section *Quiet mode*:
    - Highs hit harder and bass drops deeper with the quietest quiet of any Bose over-ear headphone yet.
- Section *Aware mode*:
    - Hear your music and surroundings with full transparency.

### 22. Stagg EKG Electric Kettle: 4 missed

https://fellowproducts.com/products/stagg-ekg-electric-pour-over-kettle

- Section *other places on the page*:
    - Weight: 1,400 g (includes kettle base).
    - EKG Pro & Pro Studio Menu Walkthrough.
- Section *A pleasure to hold*:
    - An ergonomic handle that feels natural in your hand and stays comfortable from the first pour to the last.
- Section *Clear at a glance*:
    - A high-resolution color display shows temperature and heating progress so you always know where things stand.

### 23. ZWILLING Pro 8-inch Chef's Knife: 4 missed

https://www.zwilling.com/us/zwilling-pro-8-inch-chefs-knife-38401-203/38401-203-0.html

- Section *PROFESSIONAL-QUALITY, GERMAN-MADE KNIVES THAT SET THE STANDA*:
    - Proudly made in Germany, ZWILLING Pro is our most user-friendly collection, setting the standard in comfort, balance, and ergonomics.
    - These knives were designed to support the professional pinch grip for safe and controlled cutting.
- Section *Our most user-friendly collection offers comfort, balance, a*:
    - All-Around Knife: For chopping, slicing, dicing, and more Curved Bolster: Supports professional pinch grip Triple-Rivet Handle: For comfort and control Special-Formula Steel: For sharpness and durability Made in Germany, with lifetime warranty Average rating 4.9 ( 2748 Ratings ) 22 Questions / 23
- Section *other places on the page*:
    - PROFESSIONAL-QUALITY, GERMAN-MADE KNIVES THAT SET THE STANDARD..

### 24. Lemon Lime Hydration Multiplier: 3 missed

https://www.liquid-iv.com/products/lemon-lime-hydration-multiplier

- Section *this is what we call countertop-convenience in a cup.*:
    - LEMON LIME HYDRATION MULTIPLIER®
    - 1 scoop of new Lemon Lime Hydration Multiplier® Multiserve
- Section *Ingredients*:
    - Gluten Free Non-GMO

### 25. Everyday Backpack: 3 missed

https://www.peakdesign.com/products/everyday-backpack

- Section *description*:
    - Our Everyday Line was designed and built with the protection, organization, and access needed to carry creative gear—from a single mirrorless camera to a full pro kit.
    - But unlike a dedicated camera bag, Everyday Bags are perfect for organizing anything.
    - Now your camera bag and everyday bag can be one-in-the-same.

### 26. Kore Short Lined 7" - Black: 3 missed

https://vuoriclothing.com/products/kore-short-black

- Section *Kore Short Lined 7"*:
    - Men's Athletic Shorts
- Section *Fit*:
    - Classic Fit; Ease through the hips and thighs 7" Inseam Built-in boxer brief liner for maximum comfort & versatility.
- Section *Product Features*:
    - Back Pocket Drawcord Elastic Waistband Lined Slash Pockets

### 27. Levi's Men's 501 Original Fit Button Fly Stretch Jeans: 3 missed

https://www.macys.com/shop/product/levis-mens-501-original-fit-button-fly-stretch-jeans?ID=1376664

- Section *other places on the page*:
    - Levi's Men's 501® Original Fit Button Fly Stretch Jeans.
- Section *Product Details*:
    - Available in Big & Tall.
- Section *Materials & Care*:
    - Machine washable All cotton This product contains certified organic fibers [95% organic cotton / 4% polyester / 1% elastane]

### 28. Maybelline Lash Sensational Sky High Mascara, True Black: 3 missed

https://www.amazon.com/dp/B09VCXH9GF

- Section *Maybelline Lash Sensational Sky High Mascara, Volumizing, Tr*:
    - Lengthening, Defining, Curling, Multiplying, Buildable Mascara Formula, Up to 24HR Wear Visit the MAYBELLINE Store 4.5 4.5 out of 5 stars (189,903) #1 Best Seller in Mascara
- Section *Features & Specs*:
    - Product Benefits Defining, Lengthening, Volumizing, Washable Other Special Features of the Product Lightweight, Washable Item Form Liquid Specialty Natural Container Type Tube Water Resistance Level Not Water Resistant Mascara Wand Type Flex Tower Coverage Full,Lightweight Skin Tone All Skin Type
- Section *Measurements*:
    - Number of Items 1 Item Volume 0.24 fluid ounces Item Dimensions 5.63 x 2.75 x 5.63 inches Item Weight 6.8 g

### 29. Lodge Pre-Seasoned Cast Iron Skillet 10.25 in: 3 missed

https://www.amazon.com/dp/B00006JSUA

- Section *other places on the page*:
    - Material: Cast Iron
    - Capacity: 10.25 cubic inches
- Section *Lodge Pre-Seasoned Cast Iron Skillet, PFAS-Free, 10.25 Inche*:
    - Non-Toxic & Naturally Non-Stick Cast Iron Pan, Compatible with Oven, Stove, Grill & Campfire, Made in USA Visit the Lodge Store 4.6 4.6 out of 5 stars (132,121) Amazon's Choice highlights highly rated, well-priced products available to ship immediately.

### 30. Barbour Classic Bedale Waxed Jacket: 2 missed

https://www.barbour.com/us/classic-bedale%C2%AE-waxed-jacket-MWX0010OL7148.html

- Section *Description*:
    - SKU: MWX0010OL7148 View Care & Product Information
- Section *Care & Information*:
    - Outer: 100% Cotton (Waxed) Lining: 100% Cotton 6oz waxed cotton 6oz waxed cotton Branding to left pocket Back Length: 72.5cm - 81.3cm Do not wash Sponge clean only View Delivery Information

### 31. Nalgene 32oz Wide Mouth Bottle: 2 missed

https://nalgene.com/product/32oz-wide-mouth-bottle/

- Section *other places on the page*:
    - Color: Violet New Color.
- Section *Color: Violet New Color*:
    - In stock New Color The classic BPA/BPS- free Nalgene Wide Mouth bottle is now thoughtfully made from Tritan Renew.

### 32. Rare Beauty by Selena Gomez Soft Pinch Liquid Blush: 2 missed

https://www.sephora.com/product/rare-beauty-by-selena-gomez-soft-pinch-liquid-blush-P97989778

- Section *4 payments of $6.25*:
    - bright pink Color: Adore - bright pink 3.2M Size 0.25 oz/7.5 mL Size 0.25 oz/7.5 mL Grid List Radiant finish - Standard size Radiant finish - Mini size Matte finish - Standard size Matte finish - Mini size NEW NEW NEW NEW NEW
- Section *Highlights*:
    - Medium Coverage Vegan Long-wearing Cruelty-Free Community Favorite Medium Coverage Vegan Long-wearing Cruelty-Free Community Favorite

### 33. Stanley Quencher H2.0 FlowState Tumbler 40 oz, Charcoal: 1 missed

https://www.amazon.com/dp/B0BD78MYPN

- Section *other places on the page*:
    - Product Summary: Stanley Quencher H2.0 FlowState Tumbler 40oz (Charcoal).
