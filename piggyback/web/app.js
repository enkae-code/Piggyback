"use strict";
// Every page-derived string goes in through textContent or setAttribute, never innerHTML.

const MAX_REFS = 3;
const GAP = 3; // px kept between two pin badges
const token = new URLSearchParams(location.search).get("t") || "";
history.replaceState(null, "", "/"); // keep the session token out of the address bar and history

const state = { refs: [], last: null, lastRequest: null, urls: [], busy: false };
const frames = new Map(); // frame element -> relayout function, for screenshots with pins
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (value !== false && value !== null) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...kids.filter(Boolean));
  return node;
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

function setStatus(message) {
  $("status").textContent = message;
}

async function api(path, body) {
  const init = { method: body ? "POST" : "GET", headers: { "X-Piggyback-Token": token } };
  if (body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

async function shotURL(id) {
  const res = await fetch(`/api/shot/${id}.png`, { headers: { "X-Piggyback-Token": token } });
  if (!res.ok) throw new Error("Screenshot could not be loaded.");
  const url = URL.createObjectURL(await res.blob());
  state.urls.push(url);
  return url;
}

// Reference and build slots

function renderRefs() {
  const list = $("ref-list");
  list.replaceChildren(
    ...state.refs.map((url, index) =>
      el("li", {}, el("span", { text: url }),
        el("button", { type: "button", class: "btn", "aria-label": `Remove reference ${index + 1}`,
          text: "Remove" }))),
  );
  for (const [index, button] of [...list.querySelectorAll("button")].entries()) {
    button.addEventListener("click", () => {
      state.refs.splice(index, 1);
      renderRefs();
    });
  }
  $("ref-add").disabled = state.refs.length >= MAX_REFS;
}

function addRef() {
  const input = $("ref-input");
  const url = input.value.trim();
  if (!url) return;
  if (state.refs.length >= MAX_REFS) {
    showError("ref-error", "Three references is the most. More would be averaged away.");
    return;
  }
  showError("ref-error", "");
  state.refs.push(url);
  input.value = "";
  renderRefs();
}

// Measure, re-run, copy

function carryTicks(previous, next) {
  // On a re-run, a pin that is no longer open reads as fixed. Earlier fixes stay ticked.
  if (!previous || previous.state !== "compare" || next.state !== "compare") return [];
  const open = new Set(next.pins.map((pin) => pin.concept));
  const kept = new Map();
  for (const pin of [...(previous.ticked || []), ...previous.pins]) {
    if (!open.has(pin.concept)) kept.set(pin.concept, pin);
  }
  return [...kept.values()];
}

function clock() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

async function measure(rerun) {
  if (state.busy) return;
  if (!rerun && $("ref-input").value.trim()) addRef();
  const request = rerun
    ? state.lastRequest
    : { references: [...state.refs], build: $("build-input").value.trim() || null };
  if (!request.references.length && !request.build) {
    showError("error", "Add a reference, your build, or both.");
    return;
  }
  showError("error", "");
  setBusy(true, "Measuring. A page can take up to a minute.");
  try {
    const data = await api("/api/measure", request);
    data.ticked = rerun ? carryTicks(state.last, data) : [];
    state.lastRequest = request;
    state.last = data;
    renderResult(data);
    $("rerun").hidden = false;
    $("copy").hidden = false;
    const pages = request.references.length + (request.build ? 1 : 0);
    setStatus(`${rerun ? "Re-measured" : "Measured"} ${pages} ${pages === 1 ? "page" : "pages"} · ${clock()}`);
  } catch (err) {
    showError("error", err.message);
    setStatus("");
  } finally {
    setBusy(false);
  }
}

function setBusy(busy, message) {
  state.busy = busy;
  for (const id of ["measure", "rerun", "copy"]) $(id).disabled = busy;
  $("result").setAttribute("aria-busy", String(busy));
  if (busy) setStatus(message);
}

async function copyBrief() {
  try {
    const data = await api("/api/brief", {});
    try {
      await navigator.clipboard.writeText(data.text);
      setStatus(`Wrote ${data.path} and copied it.`);
    } catch {
      const area = $("fallback");
      area.value = data.text;
      area.hidden = false;
      area.focus();
      area.select();
      setStatus(`Wrote ${data.path}. Clipboard unavailable: the text is selected below.`);
    }
  } catch (err) {
    showError("error", err.message);
  }
}

// Result views

function renderResult(data) {
  state.urls.forEach((url) => URL.revokeObjectURL(url));
  state.urls = [];
  frames.clear();
  const views = { brief: briefView, compare: compareView, defaults: defaultsView };
  $("result").replaceChildren(views[data.state](data));
}

function visualNode({ kind, items }) {
  if (kind === "swatches") {
    return el("div", { class: "swatches", "aria-hidden": "true" }, ...items.map((hex) => {
      const chip = el("span", { class: "swatch" });
      chip.style.backgroundColor = hex;
      return chip;
    }));
  }
  if (kind === "radii") {
    return el("div", { class: "radii", "aria-hidden": "true" }, ...items.map((radius) => {
      const sample = el("span", { class: "radius-sample" });
      sample.style.borderRadius = `${Math.min(radius, 24)}px`;
      return el("span", { class: "radius-cell" }, sample, el("span", { class: "scale-label", text: `${radius}px` }));
    }));
  }
  const max = Math.max(...items) || 1;
  return el("div", { class: "scale", "aria-hidden": "true" }, ...items.map((size) => {
    const bar = el("span", { class: "bar" });
    bar.style.width = `${(size / max) * 100}%`;
    return el("div", { class: "scale-row" }, el("span", { class: "scale-label", text: `${size}px` }),
      el("span", { class: "track" }, bar));
  }));
}

function lineItem(line, number, pageLevel) {
  const value = line.before
    ? el("p", { class: "value" }, el("span", { class: "before", text: line.before }),
      el("span", { class: "arrow", text: "→" }), el("span", { class: "after", text: line.after }))
    : el("p", { class: "value", text: line.detail });
  return el("li", { "data-pin": number || false, tabindex: number ? "0" : false },
    number ? el("span", { class: "pin", text: String(number) }) : null,
    el("div", { class: "item-body" },
      el("p", { class: "concept" }, el("span", { text: line.name }),
        pageLevel ? el("span", { class: "tag", text: "Page-level" }) : null),
      value,
      line.visual ? visualNode(line.visual) : null,
      line.why ? el("p", { class: "why", text: `Why it matters: ${line.why}` }) : null));
}

function briefView(data) {
  return el("div", { class: "result-body" }, ...data.references.map((ref) =>
    el("section", { class: "panel" }, el("h2", { text: ref.url }),
      shotFigure(`Reference · ${ref.url}`, ref.shot, null, [], true),
      el("ul", { class: "lines" }, ...ref.lines.map((line) => lineItem(line))))));
}

function defaultsView(data) {
  const note = data.items.length
    ? "No default has a position on the page, so no pin marks the screenshot. Numbers match the list."
    : "No generic defaults detected.";
  return el("section", { class: "panel" }, el("h2", { text: "Defaults detected" }),
    shotFigure(`Your build · ${data.build.url}`, data.build.shot, data.build.viewport, [], true),
    el("p", { class: "note", text: note }),
    el("ol", { class: "pins" }, ...data.items.map((item) => lineItem(item, item.number, true))));
}

function compareView(data) {
  const ticked = (data.ticked || []).map((pin) =>
    el("li", { class: "done" }, el("span", { class: "tick", "aria-hidden": "true", text: "✓" }),
      el("div", { class: "item-body" }, el("p", { class: "concept", text: `Fixed on re-run: ${pin.name}` }))));
  const notFound = data.not_found.length
    ? el("section", { class: "notfound-block" }, el("h3", { class: "section-title", text: "Not found" }),
      el("p", { class: "note", text: "Missing on one page, so there is nothing to compare. No pin." }),
      el("ul", { class: "notfound" }, ...data.not_found.map((text) => el("li", { text }))))
    : null;
  const pins = [
    ...data.pins.map((pin) => lineItem(pin, pin.number, !(pin.ref_box && pin.build_box))),
    ...ticked,
  ];
  const hasPageLevel = data.pins.some((pin) => !(pin.ref_box && pin.build_box));
  const notes = [data.note, hasPageLevel ? "Page-level: no position on the page, so no pin." : ""]
    .filter(Boolean).join(" ");
  return el("div", { class: "result-body" },
    el("div", { class: "compare" },
      shotFigure(`Reference · ${data.reference.url}`, data.reference.shot, data.reference.viewport,
        marks(data.pins, "ref_box"), true),
      shotFigure(`Your build · ${data.build.url}`, data.build.shot, data.build.viewport,
        marks(data.pins, "build_box"), true)),
    el("section", { class: "panel" }, el("h2", { class: "section-title", text: "Differences (build → reference)" }),
      notes ? el("p", { class: "note", text: notes }) : null,
      el("ol", { class: "pins", "aria-label": "Differences" }, ...pins),
      notFound));
}

// Screenshots and numbered pins

function marks(pins, key) {
  return pins.filter((pin) => pin[key]).map((pin) => ({ number: pin.number, box: pin[key] }));
}

function inside(box, viewport) {
  return box.x >= 0 && box.y >= 0 && box.x < viewport.width && box.y < viewport.height;
}

// Badge spots, tried in order: beside the box first (never on its content), inside only as a last resort.
function spotsFor(x, y, w, h, bw, bh) {
  return [
    [x, y - bh - 2], [x + w - bw, y - bh - 2],
    [x, y + h + 2], [x + w - bw, y + h + 2],
    [x + 3, y + 3], [x + w - bw - 3, y + 3],
  ];
}

function placeMarks(img, viewport, shown, outlines, badges) {
  if (!img.naturalWidth) return;
  const width = img.clientWidth;
  const height = img.clientHeight;
  const scale = width / viewport.width;
  const placed = [];
  shown.forEach(({ box }, index) => {
    const x = box.x * scale;
    const y = box.y * scale;
    const w = box.width * scale;
    const h = box.height * scale;
    Object.assign(outlines[index].style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
    const badge = badges[index];
    const bw = badge.offsetWidth;
    const bh = badge.offsetHeight;
    const fits = ([l, t]) => l >= 0 && t >= 0 && l + bw <= width && t + bh <= height;
    const clear = ([l, t]) => placed.every(([pl, pt]) => !(Math.abs(l - pl) < bw + GAP && Math.abs(t - pt) < bh + GAP));
    const spots = spotsFor(x, y, w, h, bw, bh);
    const [l, t] = spots.find((spot) => fits(spot) && clear(spot)) || spots.find(fits) || spots[4];
    const left = Math.max(0, Math.min(l, width - bw));
    const top = Math.max(0, Math.min(t, height - bh));
    placed.push([left, top]);
    Object.assign(badge.style, { left: `${left}px`, top: `${top}px` });
  });
}

function relayoutAll() {
  for (const [frame, relayout] of frames) {
    if (frame.isConnected) relayout();
    else frames.delete(frame);
  }
}

function shotFigure(caption, shot, viewport, pinMarks, zoomable) {
  const img = el("img", { alt: `Screenshot: ${caption}` });
  const frame = el("div", { class: "frame" }, img);
  const shown = viewport ? pinMarks.filter(({ box }) => inside(box, viewport)) : [];
  const outlines = shown.map(({ number }) => el("div", { class: "box", "data-pin": number }));
  const badges = shown.map(({ number }) => el("span", { class: "pin", "data-pin": number, text: String(number) }));
  frame.append(...outlines, ...badges);
  if (viewport) {
    const relayout = () => placeMarks(img, viewport, shown, outlines, badges);
    frames.set(frame, relayout);
    img.addEventListener("load", relayout);
  }
  shotURL(shot).then(
    (src) => { img.src = src; },
    () => frame.replaceChildren(el("p", { class: "muted", text: "Screenshot could not be loaded." })),
  );
  const cap = el("figcaption", { class: "shot-cap" }, el("span", { text: caption }));
  if (zoomable && viewport) {
    const full = el("button", { type: "button", class: "btn small", text: "Full size" });
    full.addEventListener("click", () => openZoom(caption, shot, viewport, pinMarks));
    cap.append(full);
  }
  return el("figure", { class: "shot" }, frame, cap);
}

function openZoom(caption, shot, viewport, pinMarks) {
  $("zoom-title").textContent = caption;
  $("zoom-body").replaceChildren(shotFigure(caption, shot, viewport, pinMarks, false));
  $("zoom").showModal();
  relayoutAll();
}

function highlight(event, on) {
  const item = event.target.closest("[data-pin]");
  if (!item) return;
  const number = Number(item.dataset.pin);
  for (const node of document.querySelectorAll(`[data-pin="${number}"]`)) {
    node.classList.toggle("lit", on);
  }
}

// Wiring

$("ref-add").addEventListener("click", addRef);
$("ref-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addRef();
  }
});
$("slots").addEventListener("submit", (event) => {
  event.preventDefault();
  measure(false);
});
$("rerun").addEventListener("click", () => measure(true));
$("copy").addEventListener("click", copyBrief);
$("zoom-close").addEventListener("click", () => $("zoom").close());
window.addEventListener("resize", relayoutAll);
for (const type of ["mouseover", "focusin"]) {
  $("result").addEventListener(type, (event) => highlight(event, true));
}
for (const type of ["mouseout", "focusout"]) {
  $("result").addEventListener(type, (event) => highlight(event, false));
}

if (!token) {
  showError("error", "Open the link piggyback printed: the session token is missing.");
  $("measure").disabled = true;
}
