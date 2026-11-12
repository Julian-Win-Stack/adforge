# Regression grading page proof

These screenshots show the fixture store from
`backend/tests/test_regression_grading.py`. Its videos are tiny real, coloured test
clips made locally by the existing fake model; no paid ad was made or judged.

- [Full scenario, new beside its baseline](full-scenario.png)
- [Picture scenario, with no baseline yet](picture-scenario.png)

The Chromium test checks every canonical grading control, an earlier unsure take,
the folded baseline, the exact document saved to the mocked Artifact collection,
and the layout at phone width. Separate tests check a failed save keeps its local
draft, importing grades writes the exact words and photo labels, and a real chat
with faked paid services exports its own image/video call records for this page.
The review regression test saves feedback on a historical starting picture and an
end picture, and shows code-answer reason/item changes even when their overall
verdict stays the same. A photo storage-key change alone produces no warning.

From `backend/`, regenerate the screenshots while running the browser test:

```sh
REGRESSION_GRADING_PROOF="$PWD/../docs/regression-evals-proof" \
  uv run pytest -q tests/test_regression_grading.py
```

`REGRESSION_PROOF_BROWSER` can select an installed Chromium executable. The checked-in
screenshots used the environment's system Chromium; CI uses its installed Playwright
Chromium. The test serves the page and media over a local HTTP test server.
