/* Static reader. Loads only files under ./data/ — no outbound requests.
   Comment bodies are untrusted strings, so everything goes in via
   textContent. Never innerHTML here. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const state = { index: [], meta: null, sub: null, query: "", cursor: -1, open: null };

// ------------------------------------------------------------------ time

function ago(seconds) {
  if (!seconds) return "";
  const d = Math.max(0, Date.now() / 1000 - seconds);
  if (d < 3600) return `${Math.round(d / 60)}m`;
  if (d < 86400) return `${Math.round(d / 3600)}h`;
  return `${Math.round(d / 86400)}d`;
}

function paintStrip() {
  const m = state.meta;
  if (!m) return;
  const age = (Date.now() - Date.parse(m.captured)) / 60000;
  const stale = age > 90;
  $("strip").classList.toggle("is-stale", stale);
  $("strip-main").textContent =
    `captured ${m.captured.replace("T", " ").replace("+00:00", "Z")} · ${ago(Date.parse(m.captured) / 1000)} ago`;
  $("strip-note").textContent =
    `· ${m.threads} threads · ${m.subreddits.join(" ")}` +
    (m.degraded ? " · degraded: rss fallback in use" : "");
}

// ------------------------------------------------------------------ list

function visible() {
  const q = state.query.toLowerCase();
  return state.index.filter(
    (p) =>
      (!state.sub || p.sub === state.sub) &&
      (!q || p.title.toLowerCase().includes(q))
  );
}

function paintList() {
  const list = $("list");
  list.replaceChildren();
  const rows = visible();

  if (!rows.length) {
    list.append(el("li", "list-empty", "No threads match that filter."));
    return;
  }

  rows.forEach((p, i) => {
    const li = el("li", "row");
    li.dataset.id = p.id;
    if (p.degraded) li.classList.add("is-degraded");
    if (i === state.cursor) li.classList.add("is-active");
    if (p.id === state.open) li.classList.add("is-active");

    li.append(el("div", "row-title", p.title));

    const meta = el("div", "row-meta");
    meta.append(document.createTextNode(`r/${p.sub} · ${p.score} · ${p.num_comments} comments · ${ago(p.created)}`));
    if (p.flair) {
      meta.append(document.createTextNode(" · "));
      meta.append(el("span", "flair", p.flair));
    }
    li.append(meta);

    li.addEventListener("click", () => {
      state.cursor = i;
      openThread(p.id);
    });
    list.append(li);
  });
}

// ---------------------------------------------------------------- thread

async function openThread(id) {
  const pane = $("thread");
  state.open = id;
  paintList();

  let t;
  try {
    const res = await fetch(`data/${id}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    t = await res.json();
  } catch (err) {
    $("empty").hidden = true;
    pane.hidden = false;
    pane.replaceChildren(
      el("div", "notice", `Couldn't load this thread (${err.message}). It may have dropped out of the last capture.`)
    );
    return;
  }

  $("empty").hidden = true;
  pane.hidden = false;
  pane.replaceChildren();
  document.body.classList.add("is-reading");

  const back = el("button", "back", "\u2190 threads");
  back.addEventListener("click", closeThread);
  pane.append(back);

  pane.append(el("h1", "thread-title", t.title));

  const meta = el("div", "thread-meta");
  meta.append(document.createTextNode(
    `r/${t.sub} · u/${t.author} · ${t.score} points · posted ${ago(t.created)} ago · `
  ));
  const link = el("a", null, "original");
  link.href = t.permalink;
  link.rel = "noreferrer noopener";
  meta.append(link);
  pane.append(meta);

  if (t.degraded) {
    pane.append(el("div", "notice",
      "RSS fallback: comments are flat and unscored because the JSON endpoint refused the runner."));
  }

  if (t.selftext) pane.append(el("div", "selftext", t.selftext));

  pane.append(el("div", "count", `${t.comments.length} comments`));

  for (const c of t.comments) {
    const box = el("div", "comment");
    box.dataset.depth = c.depth;
    box.style.marginLeft = `${Math.min(c.depth, 8) * 0.9}rem`;

    const cm = el("div", "comment-meta");
    if (c.op) {
      cm.append(el("span", "op", `u/${c.author}`));
      cm.append(document.createTextNode(" (OP)"));
    } else {
      cm.append(document.createTextNode(`u/${c.author}`));
    }
    cm.append(document.createTextNode(` · ${c.score} · ${ago(c.created)}`));

    box.append(cm, el("div", "comment-body", c.body));
    pane.append(box);
  }

  $("read-pane").scrollTop = 0;
  $("read-pane").focus({ preventScroll: true });
  location.hash = id;
}

function closeThread() {
  document.body.classList.remove("is-reading");
  state.open = null;
  history.replaceState(null, "", location.pathname);
  paintList();
}

// ------------------------------------------------------------- keyboard

function move(delta) {
  const rows = visible();
  if (!rows.length) return;
  state.cursor = Math.max(0, Math.min(rows.length - 1, state.cursor + delta));
  paintList();
  document.querySelector(".row.is-active")?.scrollIntoView({ block: "nearest" });
}

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  if (e.key === "j") { move(1); e.preventDefault(); }
  else if (e.key === "k") { move(-1); e.preventDefault(); }
  else if (e.key === "Enter" || e.key === "o") {
    const p = visible()[state.cursor];
    if (p) openThread(p.id);
  }
  else if (e.key === "Escape") closeThread();
  else if (e.key === "/") { $("search").focus(); e.preventDefault(); }
});

// ------------------------------------------------------------------ boot

async function boot() {
  try {
    const [index, meta] = await Promise.all([
      fetch("data/index.json").then((r) => r.json()),
      fetch("data/meta.json").then((r) => r.json()),
    ]);
    state.index = index;
    state.meta = meta;
  } catch (err) {
    $("strip-main").textContent = "no capture found — check the Actions log";
    $("strip").classList.add("is-stale");
    return;
  }

  paintStrip();
  setInterval(paintStrip, 60000);

  const subs = [...new Set(state.index.map((p) => p.sub))].sort();
  const bar = $("subs");
  const mk = (label, value) => {
    const b = el("button", "sub-btn", label);
    b.setAttribute("aria-pressed", String(state.sub === value));
    b.addEventListener("click", () => {
      state.sub = state.sub === value ? null : value;
      state.cursor = -1;
      [...bar.children].forEach((c) =>
        c.setAttribute("aria-pressed", String(c.dataset.value === state.sub))
      );
      paintList();
    });
    b.dataset.value = value ?? "";
    return b;
  };
  subs.forEach((s) => bar.append(mk(`r/${s}`, s)));

  $("search").addEventListener("input", (e) => {
    state.query = e.target.value;
    state.cursor = -1;
    paintList();
  });

  paintList();

  const hash = location.hash.slice(1);
  if (hash && state.index.some((p) => p.id === hash)) openThread(hash);
}

boot();
