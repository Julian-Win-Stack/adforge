// @vitest-environment node
import { expect, test } from "vitest";
import { nextFollowing } from "./follow";

test.each([
  ["the code's own scroll, landing after more loaded below", 1000, 1000, false, true, true],
  ["the code's own scroll, while not following", 400, 400, false, false, false],
  ["the reader scrolls up", 700, 1000, false, true, false],
  ["the reader scrolls up a few pixels", 995, 1000, false, true, false],
  ["the reader scrolls up a hair, within a pixel", 999.5, 1000, false, true, true],
  ["the reader scrolls back near the bottom", 1480, 900, true, false, true],
  ["the reader scrolls down but not near the bottom", 1200, 900, false, false, false],
  ["the reader, following again, scrolls up a second time", 1100, 1600, false, true, false],
])("%s", (_, top, placedAt, nearBottom, following, expected) => {
  expect(nextFollowing({ top, placedAt, nearBottom, following })).toBe(expected);
});
