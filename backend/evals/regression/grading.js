/* Regression views over the grading-page skill's renderer and autosave code. */
Object.assign(docs, DATA.grades);
const baseGrader = grader;
const gradeAliases = {};
for (const [k, a] of Object.entries(ADS))
  for (const s of a.scenes)
    for (const c of s.clips) gradeAliases[k + "__clip-" + c.pk] = c.gradeId;
let readingOld = false;
grader = (id, ...args) =>
  readingOld
    ? $("div", { class: "small mute" }, "Baseline record")
    : baseGrader(gradeAliases[id] || id, ...args);
const reminders = (stage) =>
  $(
    "div",
    { class: "small mute" },
    $("b", {}, "Questions to grade"),
    $(
      "ul",
      {},
      (DATA.questions[stage] || []).map((q) => $("li", {}, q)),
    ),
  );
const pictures = (a) =>
  $(
    "div",
    { class: "pics" },
    a.scenes
      .filter((s) => s.picture)
      .map((s) => img(s.picture, "Scene " + s.n + " starting picture")),
  );
function comparison(a) {
  const wrap = $(
    "div",
    { class: "box", "data-comparison": true },
    $(
      "h3",
      {},
      a.kind === "picture"
        ? "Starting pictures, old beside new"
        : "Finished ad, old beside new",
    ),
  );
  const pair = $(
    "div",
    { class: "ads2" },
    $(
      "div",
      {},
      $("p", { class: "vcap" }, "New · " + a.run),
      a.kind === "picture"
        ? pictures(a)
        : a.ad
          ? vid(a.ad)
          : $("p", {}, "No finished ad."),
    ),
  );
  if (a.old)
    pair.append(
      $(
        "div",
        {},
        $("p", { class: "vcap" }, "Old · " + a.old.run),
        a.kind === "picture"
          ? pictures(a.old)
          : a.old.ad
            ? vid(a.old.ad)
            : $("p", {}, "No finished baseline ad."),
      ),
    );
  wrap.append(
    pair,
    $("p", {}, "The new one is no worse"),
    grader(a.key + "__no-worse", a.key + " new is no worse"),
  );
  return wrap;
}
function pictureRecord(p) {
  return $(
    "div",
    {},
    p.checks.map((check) =>
      $(
        "div",
        { class: "small" },
        $(
          "span",
          { class: "chip " + (check.pass ? "c-pass" : "c-fail") },
          check.name + (check.pass ? ": yes" : ": no"),
        ),
        " ",
        check.says,
      ),
    ),
    p.status
      ? $("p", { class: "small mute" }, "Picture check: " + p.status)
      : null,
    p.ask ? det("What the scene agent asked for", false, pre(p.ask)) : null,
    imageSent(p.image),
  );
}
function sceneView(k, a, s) {
  const box = $(
    "section",
    { class: "box scene", id: (readingOld ? "old-s" : "s") + s.n },
    $("h3", {}, k + " · Scene " + s.n),
    $("p", { class: "says" }, "Says: “" + s.line + "”"),
    $("p", { class: "shows" }, "Shows: " + s.shows),
  );
  if (s.picture) {
    box.append(
      $("h4", {}, "Kept starting picture"),
      img(s.picture, "Kept starting picture"),
      reminders("pictures"),
      s.startPics.find((p) => p.kept)
        ? pictureRecord(s.startPics.find((p) => p.kept))
        : null,
      grader(k + "__s" + s.n + "__picture", k + " scene " + s.n + " picture"),
    );
  }
  const otherPictures = s.startPics.filter((p) => !p.kept);
  if (otherPictures.length) {
    box.append(
      det(
        "Other starting and end pictures, with their records",
        false,
        otherPictures.map((p) =>
          $(
            "div",
            {},
            img(p.file, p.label),
            pictureRecord(p),
            grader(p.gradeId, k + " scene " + s.n + " " + p.label),
          ),
        ),
      ),
    );
  }
  if (s.picked.length)
    box.append(
      det(
        "Photos the scene agent looked at",
        false,
        $(
          "div",
          { class: "pics" },
          s.picked.map((p) => img(p.file, p.label)),
        ),
      ),
    );
  if (s.clips.length) box.append(reminders("clips"));
  s.clips.forEach((c, i) => box.append(clipRow(k, s, c, i)));
  if (s.agent.length)
    box.append(det("Everything the scene agent did", false, calls(s.agent)));
  s.gaves.forEach((g) => box.append($("div", { class: "flag amber" }, g)));
  return box;
}
const answerItems = (answer) =>
  (answer.items || [])
    .map((item) => ({
      item: item.item,
      verdict: item.verdict,
      reason: item.reason || "",
    }))
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
const answerMeaning = (answer) =>
  JSON.stringify({
    verdict: answer.verdict,
    reason: answer.reason || "",
    items: answerItems(answer),
  });
const verdictWords = (verdict) =>
  ({
    pass: "passes",
    fail: "fails",
    needs_label: "needs label",
    not_reached: "not reached",
  })[verdict] || verdict;
adView = (k) => {
  const a = ADS[k],
    w = $("div", { class: "wrap" });
  w.append(
    $(
      "div",
      { class: "box" },
      $("h2", {}, k),
      $(
        "div",
        { class: "links" },
        a.url
          ? $(
              "a",
              { href: a.url, target: "_blank", rel: "noopener" },
              "Product page",
            )
          : null,
        $("span", {}, money(a.cost)),
        a.trace
          ? $(
              "a",
              { href: a.trace, target: "_blank", rel: "noopener" },
              "Langfuse trace",
            )
          : null,
      ),
      $(
        "p",
        { class: "small mute" },
        "No feedback counts as pass. Your words and taps save as you type.",
      ),
      a.reencoded
        ? $(
            "p",
            { class: "small mute" },
            "This scenario’s page uses re-encoded copies of " +
              a.reencoded +
              " videos to fit the host’s file limit.",
          )
        : null,
    ),
  );
  const changes = [];
  if (a.old)
    for (const [id, newAnswer] of Object.entries(a.code)) {
      const old = a.old.code[id];
      if (old && answerMeaning(old) !== answerMeaning(newAnswer))
        changes.push(
          "new: " +
            id +
            " " +
            verdictWords(newAnswer.verdict) +
            " — " +
            newAnswer.reason +
            (JSON.stringify(answerItems(old)) !==
            JSON.stringify(answerItems(newAnswer))
              ? answerItems(newAnswer)
                  .map(
                    (item) =>
                      "; " +
                      item.item +
                      " " +
                      verdictWords(item.verdict) +
                      (item.reason ? " — " + item.reason : ""),
                  )
                  .join("")
              : ""),
        );
    }
  if (changes.length)
    w.append(
      $(
        "div",
        { class: "flag info" },
        changes.map((x) => $("p", { style: "margin:0" }, x)),
      ),
    );
  if (a.reds.length || a.answers.length)
    w.append(
      $(
        "div",
        { class: "box" },
        $("h3", {}, "Worth knowing before you watch"),
        a.reds.map((warning) =>
          $(
            "div",
            { class: "flag red" },
            $("b", {}, warning.kind),
            warning.text,
          ),
        ),
        a.answers.map((answer) =>
          $(
            "div",
            { class: "flag info" },
            $("b", {}, "Shop-owner answer: " + answer.a),
            answer.q,
          ),
        ),
      ),
    );
  w.append(comparison(a));
  if (a.ad)
    w.append(
      $(
        "div",
        { class: "box" },
        $("h3", {}, "Your thoughts on the finished ad"),
        reminders("ad"),
        grader(k + "__ad", k + " finished ad", true),
      ),
    );
  if (a.photos.length) {
    const p = $(
      "div",
      { class: "box" },
      $("h3", {}, "Page photos needing a label"),
    );
    a.photos.forEach((photo) => {
      const g = grader(photo.id, k + " product photo");
      g.querySelector('[data-v="pass"]').textContent = "This product";
      g.querySelector('[data-v="fail"]').textContent = "Not this product";
      p.append(img(photo.file, "Page photo"), g);
    });
    w.append(p);
  }
  if (a.hasPage)
    w.append(
      $(
        "div",
        { class: "box" },
        $("h3", {}, "The page read"),
        det("Facts kept from the page", false, pre(a.pageText)),
        reminders("page"),
        grader(k + "__page", k + " page read"),
      ),
    );
  if (a.scenes.length)
    w.append(
      $(
        "div",
        { class: "box" },
        $("h3", {}, "The plan"),
        a.scenes.map((s) =>
          $(
            "div",
            {},
            $("b", {}, "Scene " + s.n),
            $("p", {}, "Says: “" + s.line + "”"),
            $("p", { class: "small mute" }, "Shows: " + s.shows),
          ),
        ),
        reminders("plan"),
        grader(k + "__plan", k + " plan"),
      ),
    );
  a.scenes.forEach((s) => w.append(sceneView(k, a, s)));
  a.notes.forEach((n) =>
    w.append(
      $(
        "div",
        { class: "box" },
        $("h3", {}, "Owner note " + n.number),
        $("p", {}, n.text),
        $(
          "div",
          { class: "ads2" },
          $(
            "div",
            {},
            $("p", { class: "vcap" }, "Before"),
            n.before ? vid(n.before) : null,
          ),
          $("div", {}, $("p", { class: "vcap" }, "After"), vid(n.after)),
        ),
        reminders("note"),
        grader(k + "__note" + n.number, k + " owner note " + n.number),
      ),
    ),
  );
  if (a.old) {
    readingOld = true;
    const old = det(
      "Old scenes and records · " + a.old.run,
      false,
      a.old.scenes.map((s) => sceneView(k, a.old, s)),
      Object.entries(a.old.grades || {})
        .filter(([id, d]) => id.startsWith(k + "__") && (d.grade || d.note))
        .map(([id, d]) =>
          $(
            "div",
            { class: "flag info" },
            $(
              "b",
              {},
              "Your baseline words · " + id.split("__").slice(1).join(" "),
            ),
            d.grade ? "Tap: " + d.grade + ". " : "",
            d.note,
          ),
        ),
      everything(a.old),
    );
    readingOld = false;
    w.append(old);
  }
  w.append(everything(a));
  setTimeout(refreshNav, 0);
  return w;
};
function gradesSnapshot() {
  const all = {};
  for (const id of new Set([...Object.keys(docs), ...Object.keys(drafts)])) {
    const d = cur(id);
    all[id] = {
      grade: d.grade || null,
      note: d.note || "",
      at: d.at || new Date().toISOString(),
    };
  }
  return all;
}
function downloadGrades() {
  const link = $("a", {
    download: "grades.json",
    href: URL.createObjectURL(
      new Blob([JSON.stringify(gradesSnapshot(), null, 2)], {
        type: "application/json",
      }),
    ),
  });
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
startView = () =>
  $(
    "div",
    { class: "wrap" },
    $(
      "div",
      { class: "box" },
      $("h2", {}, "Regression grading · " + DATA.run),
      $(
        "p",
        { class: "lead" },
        "Old and new on the same inputs. No feedback counts as pass; only you judge quality.",
      ),
      stLine,
      $("p", { id: "prog" }),
      $(
        "button",
        { class: "jump", type: "button", on: { click: downloadGrades } },
        "Download grades",
      ),
      grader(
        "round__notes",
        "Your thoughts on this regression run",
        true,
        true,
      ),
    ),
    $(
      "div",
      { class: "box" },
      $("h3", {}, "Grade these first"),
      KEYS.flatMap((key) =>
        ADS[key].scenes.flatMap((scene) => {
          const flags = [];
          if (scene.gave) flags.push({ rank: 0, label: "Agent gave up" });
          if (scene.clips.some((c) => c.verdict === "fail"))
            flags.push({ rank: 1, label: "Judge failed a take" });
          if (scene.clips.some((c) => c.verdict === "unsure"))
            flags.push({ rank: 2, label: "Judge was unsure" });
          return flags.map((flag) => ({ ...flag, key, scene }));
        }),
      )
        .sort((a, b) => a.rank - b.rank)
        .map((f) =>
          $(
            "button",
            {
              class: "jump",
              type: "button",
              on: { click: () => go(f.key, "s" + f.scene.n, "s" + f.scene.n) },
            },
            f.key + " · Scene " + f.scene.n + " · " + f.label,
          ),
        ),
    ),
    KEYS.map((k) =>
      $(
        "button",
        { class: "jump", type: "button", on: { click: () => go(k) } },
        k,
      ),
    ),
  );
refreshNav = () => {
  let noted = 0;
  for (const k of KEYS) {
    const count = ADS[k].tapIds.filter(touched).length;
    if (count) noted++;
    const b = navBtns[k];
    if (b) b.querySelector(".pr").textContent = count + " noted";
  }
  const p = document.getElementById("prog");
  if (p)
    p.textContent =
      noted + " of " + KEYS.length + " scenarios have something from you";
};
(function () {
  const rail = document.getElementById("rail");
  rail.append(
    $("h1", {}, "Regression grading"),
    $("p", { class: "sub" }, DATA.run + " · " + KEYS.length + " scenarios"),
  );
  const sel = $(
    "select",
    {
      class: "navsel",
      id: "navsel",
      "aria-label": "Choose an ad",
      on: { change: (e) => go(e.target.value) },
    },
    $("option", { value: "start" }, "Start here"),
    KEYS.map((k) => $("option", { value: k }, k)),
  );
  rail.append(sel);
  const list = $("div", { class: "rlist" });
  for (const k of ["start", ...KEYS]) {
    const button = $(
      "button",
      { class: "navb", type: "button", on: { click: () => go(k) } },
      $("span", { class: "nm" }, k === "start" ? "Start here" : k),
      $("span", { class: "pr" }),
    );
    navBtns[k] = button;
    list.append(button);
  }
  rail.append(list);
  const [key, anchor] = decodeURIComponent(location.hash.slice(1)).split("/");
  const clip = ADS[key]?.scenes
    .flatMap((s) => s.clips)
    .find((c) => "clip-" + c.pk === anchor);
  go(ADS[key] ? key : "start", clip ? "t-" + clip.gradeId : anchor, anchor);
})();
