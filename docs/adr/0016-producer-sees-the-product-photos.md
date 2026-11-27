# The producer sees the product photos

Prototype, Nov 27, 2026, for [#769](https://github.com/Julian-Win-Stack/adforge/issues/769) (spec [#767](https://github.com/Julian-Win-Stack/adforge/issues/767)).

The producer now gets the page's text and its product photos, as real pictures labelled by library
number, in the answer of `read_page` (`agents/page_for_producer.py`). Until now its conversation
never held pictures, and a look answered in words.

Why: for a product-shots ad the producer writes the scenes itself and names the photo each one
starts on, and for the plan card it writes 3 plans built on what the listing has that similar
products don't. Both need the page and the photos in front of it. A look per photo would cost a
call each and answer in words that lose what makes a photo usable as a first frame.

Looks stay for everything else: starting pictures, clips, versions. At most 12 photos are given,
and they leave the conversation once the call folds.
