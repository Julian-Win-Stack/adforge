# Text filter check: leaked and missed sentences

Method: big Firecrawl text, cut into sections at headings; gpt-5-mini labels each section, given the product name and the page's own description (fix test, 2026-09-30, saved run `sections-mini`). Same 67 pages as the scored table.

Mark each line: write **OK** if our label is right, **WRONG** if not.

## A. Leaked: other-product sentences the filter kept (11)

Our label says: about another product. The filter kept it anyway.

### small-13-cymbiotika-liposomal-vitamin-c

1. [section: Got questions?     We have answers!]  
   "The Role of Sesbania grandiflora-Derived Biotin and Bambusa arundinacea-Derived Silica Extracts in Promoting Hair, Skin, and Nail Health: A Randomized, Double-Blind, Placebo-Controlled Clinical Study"  
   Your mark: 

### small-25-graza-sizzle-olive-oil

2. [section: Extra Virgin Olive Oil]  
   "Get It In A Spray"  
   Your mark: 

### amazon-06-fire-tv-stick-4k-plus

3. [section: Complete your purchase with these smart picks]  
   "Made for Amazon, USB Power Cable (Eliminates the Need for AC Adapter)"  
   Your mark: 
4. [section: Complete your purchase with these smart picks]  
   "Amazon Fire TV Alexa Voice Remote Pro (newest model) with remote finder, TV controls, and backlit buttons"  
   Your mark: 

### other-18-lifeextension-two-per-day

5. [section: Does it contain iron supplements?]  
   "However, please note the caution on the product label: Do not take this product unless you are truly deficient in iron."  
   Your mark: 

### other-19-paulaschoice-2pct-bha

6. [section: FAQ]  
   "CLEAR Extra Strength 2% BHA:"  
   Your mark: 
7. [section: FAQ]  
   "Same formula as SKIN PERFECTING."  
   Your mark: 
8. [section: FAQ]  
   "Targets stubborn acne, part of a 3-step system."  
   Your mark: 
9. [section: FAQ]  
   "CLEAR Regular Strength 2% BHA:"  
   Your mark: 
10. [section: FAQ]  
   "pH 3.9, suitable for sensitive skin and mild breakouts."  
   Your mark: 
11. [section: FAQ]  
   "For combination/oily skin, with added antioxidants for anti-aging."  
   Your mark: 

## B. Missed: real product sentences the filter dropped (41)

Our label says: real info about this product. The filter dropped it.

### small-06-necessaire-body-wash

1. [section: [The Body Lotion 450 ml \| Multi-Peptide](https://necessaire.com/products/the-body-lotion-450-ml-multi-peptide?variant=41316236066929&_rdiscovery-handle=the-body-lotion-450-ml-multi-peptide&_rdiscovery-widget=111606)] — filter said `other_product`  
   "Fragrance-Free / 450 ml"  
   Your mark: 
2. [section: [The Body Serum \| 0.5% Hyaluronic Acid](https://necessaire.com/products/the-body-serum?variant=30673952899187&_rdiscovery-handle=the-body-serum&_rdiscovery-widget=111606)] — filter said `other_product`  
   "Fragrance-Free / 150 ml"  
   Your mark: 
3. [section: [The Body Cream \| Barrier Complex](https://necessaire.com/products/the-body-cream-multi-ceramide?variant=41542375702641&_rdiscovery-handle=the-body-cream-multi-ceramide&_rdiscovery-widget=111606)] — filter said `other_product`  
   "Fragrance-Free / 200 ml"  
   Your mark: 

### small-07-tower28-sos-rescue-spray

4. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Before and after comparison of skin with blemishes on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/BA16.png?v=1774574385&width=2000)"  
   Your mark: 
5. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Before and after comparison of skin with blemishes on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/BA17.png?v=1774574385&width=2000)"  
   Your mark: 
6. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Before and after comparison of skin with blemishes on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/BA23.png?v=1774574385&width=2000)"  
   Your mark: 
7. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Before and after comparison of skin with blemishes on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/BA20.png?v=1774574385&width=2000)"  
   Your mark: 
8. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Three sizes of Tower 28 bottles on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/Frame1688331d0ad02-7932-4e98-8370-9c8f560bdc9f.jpg?v=1774574385&width=2000)"  
   Your mark: 
9. [section: Estimated Subtotal:] — filter said `not_product_info`  
   "![Before and after comparison of skin with blemishes on an orange background [Shared]](https://www.tower28beauty.com/cdn/shop/files/BA16.png?v=1774574385&width=150)"  
   Your mark: 
10. [section: [Swipe Serum Concealer®](https://www.tower28beauty.com/products/swipe-serum-concealer)] — filter said `other_product`  
   "[ ![[Duo]](https://www.tower28beauty.com/cdn/shop/files/HEROIMAGE-1d4f508a4-73c7-474e-b8ca-b067edf4807c.png?v=1774803592&width=400)![Tower 28 Beauty SOS Rescue Spray in Refill size. [Refill]](https://www.tower28beauty.com/cdn/shop/files/SOS20Spray20E28094C2A01620oz.webp?v=1774574385&width=400)![Tower 28 Beauty SOS Rescue Spray in 1oz. [1oz]](https://www.tower28beauty.com/cdn/shop/files/SOS20Spray20E28094C2A0120oz.webp?v=1774574385&width=400)![[Shared]](https://www.tower28beauty.com/cdn/shop/files/SOSSPRAY-OGf9587118-eb21-4e70-8cb4-e813a13165dc.png?v=1774803592&width=400)"  
   Your mark: 
11. [section: Follow us [@tower28beauty](https://www.instagram.com/tower28beauty "https://www.instagram.com/tower28beauty")] — filter said `not_product_info`  
   "![[A close up of the Tower 28 Beauty SOS Rescue Spray being spritzed out of the bottle's nozzle.]](https://www.tower28beauty.com/cdn/shop/files/Tower-insta230460e79-ac78-4333-8d9f-79f4c4a68dd6.jpg?v=1690919837&width=400)"  
   Your mark: 

### small-08-liquid-iv-lemon-lime

12. [section: One-time Order] — filter said `not_product_info`  
   "1 scoop of new Lemon Lime Hydration Multiplier® Multiserve"  
   Your mark: 

### small-13-cymbiotika-liposomal-vitamin-c

13. [section: Test 100+   Biomarkers] — filter said `other_product`  
   "Immunity, collagen, and antioxidant power — and it's gentle on your stomach."  
   Your mark: 

### small-17-satechi-3in1-qi2-stand

14. [section: (no heading)] — filter said `not_product_info`  
   "Easy Returns • 2 Year Warranty"  
   Your mark: 

### small-19-moft-snap-on-stand-wallet

15. [section: [Trackable Wallet Stand](https://www.moft.com/products/trackable-snap-on-phone-stand-wallet)] — filter said `other_product`  
   "ChargingWireless charging (one charge for 6 months use)"  
   Your mark: 
16. [section: [Snap Field Wallet](https://www.moft.com/products/snap-field-wallet)] — filter said `other_product`  
   "Light PinkMisty CoveBrownieTerracottaPeonyNavy BlueTaupeJet BlackSapphireClayOz GreenShow More Colors"  
   Your mark: 
17. [section: Snap Field Wallet] — filter said `other_product`  
   "Wallet with a Stand / Light PinkWallet with a Stand / Misty CoveWallet with a Stand / BrownieWallet with a Stand / TerracottaWallet with a Stand / PeonyWallet with a Stand / Navy BlueWallet with a Stand / TaupeWallet with a Stand / Jet BlackWallet with a Stand / SapphireWallet with a Stand / ClayWallet with a Stand / Oz GreenWallet without a Stand / Light PinkWallet without a Stand / Misty CoveWallet without a Stand / BrownieWallet without a Stand / TerracottaWallet without a Stand / PeonyWallet without a Stand / Navy BlueWallet without a Stand / TaupeWallet without a Stand / Jet BlackWallet without a Stand / SapphireWallet without a Stand / ClayWallet without a Stand / Oz GreenTrackable Wallet with a Stand / Misty CoveTrackable Wallet with a Stand / TerracottaTrackable Wallet with a Stand / Jet BlackTrackable Wallet without a Stand / Misty CoveTrackable Wallet without a Stand / TerracottaTrackable Wallet without a Stand / Jet BlackTrackable Wallet with a Stand / PeonyTrackable Wallet without a Stand / Peony"  
   Your mark: 

### small-22-caraway-cookware-set

18. [section: Mobile search] — filter said `not_product_info`  
   "12-pc non-stick ceramic cookware set with storage"  
   Your mark: 

### small-28-wild-one-harness-walk-kit

19. [section: [Waterproof Dog Collar](https://wildone.com/products/waterproof-dog-collar?variant=44878752481475&_rdiscovery-handle=waterproof-dog-collar&_rdiscovery-widget=241627)] — filter said `other_product`  
   "Retro / SRetro / MRetro / LBubblegum / SSpruce / SSpruce / MMoss / SMoss / MMoss / XSNavy / SNavy / MNavy / LNavy / XSNavy / XLBlack / SBlack / MBlack / LBlack / XSLunar / SLunar / MLimeade / SStrawberry / MStrawberry / XSStrawberry / XLSeafoam / SSeafoam / MSeafoam / LBlaze / SBlaze / MBlaze / LOrchid / MLilac / SLilac / LLilac / XSHudson / SHudson / MHudson / XSHudson / XLBlush / SBlush / LGray / MButter / MTan / M"  
   Your mark: 
20. [section: [Cushioned Dog Harness](https://wildone.com/products/cushioned-dog-harness?variant=41023553175747&_rdiscovery-handle=cushioned-dog-harness&_rdiscovery-widget=19180)] — filter said `other_product`  
   "Cocoa / XSCocoa / SCocoa / MCocoa / LRetro / XSRetro / SRetro / MRetro / LBubblegum / XSLilac / XSLilac / SLilac / MLilac / LBlack / XSBlack / SBlack / MBlack / LNavy / XSNavy / SNavy / MNavy / LMoonstone / MMoonstone / LSpruce / XSSpruce / SSpruce / MSpruce / L"  
   Your mark: 
21. [section: [Waterproof Dog Leash](https://wildone.com/products/waterproof-dog-leash?variant=41023561236675&_rdiscovery-handle=waterproof-dog-leash&_rdiscovery-widget=19180)] — filter said `other_product`  
   "Cocoa / StandardBubblegum / StandardBubblegum / SmallRetro / StandardRetro / SmallLilac / StandardNavy / StandardBlack / StandardSeafoam / SmallLunar / Standard"  
   Your mark: 

### small-31-petlibro-granary-wifi-feeder

22. [section: Check your phone!] — filter said `other_product`  
   "Single Bowl / White"  
   Your mark: 

### small-36-cotopaxi-allpa-35l

23. [section: Durably Designed. Built To Last.] — filter said `not_product_info`  
   "We stand behind our products."  
   Your mark: 
24. [section: Durably Designed. Built To Last.] — filter said `not_product_info`  
   "If there's a problem with your product, we'll make things right."  
   Your mark: 

### small-38-vuori-kore-short-black

25. [section: (no heading)] — filter said `not_product_info`  
   "Breathable, Soft & Lightweight"  
   Your mark: 

### amazon-02-maybelline-sky-high-mascara

26. [section: Similar items that may deliver to you quickly] — filter said `other_product`  
   "Sustainability features for this product"  
   Your mark: 

### other-17-zwilling-pro-chefs-knife

27. [section: Q: What degree is the edge on your pro collection?] — filter said `not_product_info`  
   "The ZWILLING Pro Collection features a factory edge angle of 15 degrees per side, which equals a 30-degree total inclusive cutting angle."  
   Your mark: 
28. [section: Q: What degree is the edge on your pro collection?] — filter said `not_product_info`  
   "If your Pro Collection knife has an Asian-style blade profile, such as a Santoku, the factory edge angle is more acute at approximately 9–12 degrees per side."  
   Your mark: 
29. [section: Q: I recently bought a Twin Signature set with a block, but the chefs knife is way to big and heavy for my small hands. The only small blade chefs knives I see are "Pro". Will a Pro series knife fit in a standard block used for twin signature knives?] — filter said `not_product_info`  
   "The ZWILLING Pro series knives are designed differently from the Twin Signature line, and in most cases, Pro knives will not fit perfectly in a Twin Signature knife block."  
   Your mark: 
30. [section: Q: I recently bought a Twin Signature set with a block, but the chefs knife is way to big and heavy for my small hands. The only small blade chefs knives I see are "Pro". Will a Pro series knife fit in a standard block used for twin signature knives?] — filter said `not_product_info`  
   "We recommend using a matching block designed for the Pro series to ensure a proper and secure fit."  
   Your mark: 
31. [section: Q: How much does IT weight] — filter said `not_product_info`  
   "The ZWILLING Pro 8-inch Chef’s Knife weighs approximately 0.55 lbs, as noted in the Specifications tab on the product page."  
   Your mark: 
32. [section: Q: How much does IT weight] — filter said `not_product_info`  
   "This weight offers an ideal balance between control and efficiency, light enough for precise chopping, slicing, and dicing, yet substantial enough to let the blade’s weight assist when working through denser ingredients."  
   Your mark: 
33. [section: Q: How much does IT weight] — filter said `not_product_info`  
   "Combined with its curved bolster and ergonomic handle, many cooks find the knife comfortable to use for extended periods without fatigue."  
   Your mark: 
34. [section: Q: Do the handles come in different colors?] — filter said `not_product_info`  
   "A: Only a few pieces in White, LE BLANC"  
   Your mark: 
35. [section: Q: What degree should this knife be sharpened?] — filter said `not_product_info`  
   "A: 15 to 20 Degrees on each side."  
   Your mark: 

### other-19-paulaschoice-2pct-bha

36. [section: Research] — filter said `other_product`  
   "Exfoliants balance skin and shed built-up layers, enhancing skincare effectiveness."  
   Your mark: 
37. [section: Research] — filter said `other_product`  
   "reduce pores & control oil"  
   Your mark: 
38. [section: Research] — filter said `other_product`  
   "\Based on an independent clinical study with 32 subjects after 4 weeks."  
   Your mark: 
39. [section: Research] — filter said `other_product`  
   "to improve skin barrier"  
   Your mark: 
40. [section: Research] — filter said `other_product`  
   "\Based on an independent clinical study of 29 subjects."  
   Your mark: 
41. [section: Research] — filter said `other_product`  
   "reduce pores& control oil"  
   Your mark: 
