"use strict";
// Every page-derived string goes in through textContent or setAttribute, never innerHTML.

const MAX_REFS = 3;
const token = new URLSearchParams(location.search).get("t") || "";
history.replaceState(null, "", "/"); // keep the session token out of the address bar and history

const state = { refs: [], last: null, lastRequest: null, urls: [], busy: false };
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
    setStatus(rerun ? "Re-measured." : "Measured.");
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
  const views = { brief: briefView, compare: compareView, defaults: defaultsView };
  $("result").replaceChildren(views[data.state](data));
}

function lineItem(line, number) {
  return el("li", { "data-pin": number || false, tabindex: number ? "0" : false },
    number ? el("span", { class: "pin", text: String(number) }) : null,
    el("div", {}, el("p", { class: "name", text: line.name }), el("p", { class: "detail", text: line.detail }),
      line.why ? el("p", { class: "why", text: `Why it matters: ${line.why}` }) : null));
}

function briefView(data) {
  return el("div", { class: "result-body" }, ...data.references.map((ref) =>
    el("section", { class: "panel" }, el("h2", { text: ref.url }),
      shotFigure(`Reference · ${ref.url}`, ref.shot, null, []),
      el("ul", { class: "lines" }, ...ref.lines.map((line) => lineItem(line))))));
}

function defaultsView(data) {
  const note = data.items.length
    ? "No default has a position on the page, so none is marked on the screenshot."
    : "No generic defaults detected.";
  return el("section", { class: "panel" }, el("h2", { text: "Defaults detected" }),
    shotFigure(`Your build · ${data.build.url}`, data.build.shot, data.build.viewport, []),
    el("p", { class: "note", text: note }),
    el("ol", { class: "pins" }, ...data.items.map((item) => lineItem(item, item.number))));
}

function compareView(data) {
  const ticked = (data.ticked || []).map((pin) =>
    el("li", { class: "done" }, el("span", { class: "tick", "aria-hidden": "true", text: "✓" }),
      el("div", {}, el("p", { class: "name", text: `Fixed on re-run: ${pin.name}` }))));
  const notFound = data.not_found.length
    ? el("div", { class: "panel" }, el("h2", { class: "section-title", text: "Not found" }),
      el("ul", { class: "notfound" }, ...data.not_found.map((text) => el("li", { text }))))
    : null;
  const pins = [...data.pins.map((pin) => lineItem(pin, pin.number)), ...ticked];
  return el("div", { class: "result-body" },
    el("div", { class: "compare" },
      shotFigure(`Reference · ${data.reference.url}`, data.reference.shot, data.reference.viewport,
        marks(data.pins, "ref_box")),
      shotFigure(`Your build · ${data.build.url}`, data.build.shot, data.build.viewport,
        marks(data.pins, "build_box"))),
    el("section", { class: "panel" }, el("h2", { class: "section-title", text: "Differences (build → reference)" }),
      data.note ? el("p", { class: "note", text: data.note }) : null,
      el("ol", { class: "pins", "aria-label": "Differences" }, ...pins),
      notFound));
}

// Screenshots and numbered pins

function marks(pins, key) {
  return pins.filter((pin) => pin[key]).map((pin) => ({ number: pin.number, box: pin[key] }));
}

function shotFigure(caption, shot, viewport, pinMarks) {
  const img = el("img", { alt: `Screenshot: ${caption}` });
  const frame = el("div", { class: "frame" }, img);
  if (viewport) {
    for (const { number, box } of pinMarks) {
      const inside = box.x >= 0 && box.y >= 0 && box.x < viewport.width && box.y < viewport.height;
      if (!inside) continue;
      const outline = el("div", { class: "box", "data-pin": number },
        el("span", { class: "pin", text: String(number) }));
      frame.append(outline);
      // CSSOM assignment is allowed under the page's CSP; a style attribute would not be.
      Object.assign(outline.style, {
        left: `${(box.x / viewport.width) * 100}%`,
        top: `${(box.y / viewport.height) * 100}%`,
        width: `${(box.width / viewport.width) * 100}%`,
        height: `${(box.height / viewport.height) * 100}%`,
      });
    }
  }
  shotURL(shot).then(
    (src) => { img.src = src; },
    () => frame.replaceChildren(el("p", { class: "muted", text: "Screenshot could not be loaded." })),
  );
  return el("figure", { class: "shot" }, frame, el("figcaption", { text: caption }));
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
