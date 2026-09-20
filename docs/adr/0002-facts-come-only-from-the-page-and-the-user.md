# Facts come only from the product page and the user

The agent has no web search and no tool that reaches outside the stored page text for a fact. This is
deliberate and is the founding rule of the project: the failure that motivated the rebuild was the
wrong price appearing in 2 of 3 ads that showed a price. The reference product does search the web when
extraction fails, and puts the number it finds on screen with the same confidence as one read from the
page.

When the page does not say something and the user has not said it, the agent asks. The two permitted
sources are the page text as stored with the job, and the user in the chat: a line the user types is
used as written, and a detail the user supplies, such as a promo code, is theirs to be right about.

The cost is speed. Asking the user for a price takes a round trip where a search would not. That is
accepted: asking costs one message, getting it wrong costs the ad.
