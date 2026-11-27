# The producer sees the product photos

Approved by Julian on Nov 26, 2026, in spec [#767](https://github.com/Julian-Win-Stack/adforge/issues/767) ([#769](https://github.com/Julian-Win-Stack/adforge/issues/769)). Extends [0010](0010-one-agent-after-the-first-clip.md).

Until now the producer knew of a read page only its link, the page check's reason and how many
photos were kept. Its conversation never held a picture: it saw one through a look, which answers
in words.

Now, once the page is read, the producer is given the page's text (its words, prices and sale,
product options), the shop's words from its product videos, and the product photos as real
pictures labelled by library number. They come in the answer of `read_page`, of `use_photos` when
the shop owner adds photos, and of `see_page`, which the producer calls after a page is pasted in
the page panel. [`with_the_page`](../../backend/agents/page_for_producer.py)

Why: for a product-shots ad the producer writes the scenes itself and names the photo each one
starts on, and the story is built on what is most unusual in the listing. Both need the page and
the photos in front of it. A look per photo would cost a call each and answer in words that lose
what makes a photo a good first frame.

Turned down: a look per photo (above), and giving the page in every turn's snapshot (it would be
paid for on every turn and never fold).

Cost: at most 12 photos are given, and each is sent again on every turn until the call folds
(agents/fold.py). Looks stay for everything else: starting pictures, clips, versions. Revisit if
the producer's turns grow too slow or costly, or if the photos are found to steer the
person-talking planner's inputs (they don't today: the planner is given what it was before).
