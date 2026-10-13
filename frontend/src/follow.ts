/** Whether the thread should keep following the newest message after a scroll event.
 * `top` is where the thread is now, `placedAt` where it was at the last scroll event or the
 * last time the code put it at the bottom. Near the bottom always follows; scrolling up
 * from there stops following; anything else, like the code's own scroll event landing
 * after more loaded below, changes nothing. */
export function nextFollowing({
  top,
  placedAt,
  nearBottom,
  following,
}: {
  top: number;
  placedAt: number;
  nearBottom: boolean;
  following: boolean;
}): boolean {
  if (nearBottom) return true;
  if (top < placedAt - 1) return false;
  return following;
}
