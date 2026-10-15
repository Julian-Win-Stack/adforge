// What one tap block saves (the page's only logic with a rule in it; tests/test_round_results.py
// runs it in node). `saved` is the block's saved document, `patch` the tap, `typed` the words
// in its box now: they're saved with every tap, so a tap never wipes words typed but not yet
// saved. A cause is kept only on a failed clip.
function nextDoc(saved, patch, typed, now) {
  const next = { grade: null, cause: null, note: "", ...(saved || {}), ...patch, note: typed, at: now };
  if (next.grade !== "fail") next.cause = null;
  return next;
}
