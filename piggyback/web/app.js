"use strict";
// Every page-derived string goes in through textContent or setAttribute, never innerHTML.

const MAX_REFS = 3;
const GAP = 6; // px kept between two pin badges in the rail
const RING = 2; // px an outline stands off its box, so the stroke frames the content instead of covering it
const TYPE_CAP = 72; // px: the largest type sample drawn; a bigger scale shrinks in proportion
// One line at every size, so close sizes show as a step in length. The second part shows only where the whole line fits.
const TYPE_SAMPLE = ["Every size has a job", ", and this line shows how each one reads."];
const CORNER = 40; // px: the largest corner drawn on a 96px box; radii are enlarged up to 400% so close ones differ
const PILL = 100; // ponytail: a radius this large is drawn as a pill; the data has no box height to prove it
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
        el("button", { type: "button", class: "chip-x", "aria-label": `Remove reference ${index + 1}`,
          text: "×" }))),
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
    setStatus(`${rerun ? "Re-measured" : "Measured"} ${count(pages, "page")} at ${clock()}`);
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
  $("status").classList.toggle("busy", busy);
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

// Text helpers

const bare = (url) => url.replace(/^https?:\/\//, "").replace(/\/$/, "");
const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const px = (value) => `${Number(value.toFixed(2))}px`;
const pxValues = (text) => [...text.matchAll(/([\d.]+)px/g)].map((match) => Number(match[1]));
const count = (number, one, many = `${one}s`) => `${number} ${number === 1 ? one : many}`;

// "Type scale (the set of text sizes a page uses)" -> ["Type scale", "The set of text sizes a page uses."]
function concept(name) {
  const match = /^(.*?) \((.*)\)$/.exec(name);
  return match ? [match[1], `${cap(match[2])}.`] : [name, ""];
}

// Specimens: each measured value is drawn as a sample, with the value as text beside it.

function samples(kind, items, draw) {
  return el("ul", { class: `specimen ${kind}` }, ...items.map((item) => {
    const [sample, ...labels] = draw(item);
    if (!kind.startsWith("fonts")) sample.setAttribute("aria-hidden", "true");
    return el("li", {}, sample, ...labels);
  }));
}

const unit = (text, quiet) => el("span", { class: quiet ? "unit quiet" : "unit", text });
const scaleNote = (k, what) => (Math.round(k * 100) === 100 ? null
  : el("p", { class: "note", text: `${what} drawn at ${Math.round(k * 100)}% of the measured size.` }));

function typeSpecimen(line) {
  const sizes = line.visual ? line.visual.items : pxValues(line.detail);
  if (!sizes.length) return [];
  const k = Math.min(1, TYPE_CAP / Math.max(...sizes));
  return [samples("rows type", sizes, (size) => {
    const sample = el("span", { class: "aa" }, ...TYPE_SAMPLE.map((text) => el("span", { text })));
    sample.style.fontSize = `${size * k}px`;
    return [sample, unit(px(size))];
  }), scaleNote(k, "Samples")];
}

function paletteSpecimen(line) {
  const entries = [];
  for (const part of line.detail.split(";")) {
    const role = part.trim().split(" ")[0];
    for (const [, hex, alpha, share] of part.matchAll(/(#[0-9a-f]{6})(?: at (\d+)% opacity)? \((<?\d+%)\)/gi)) {
      entries.push({ hex, alpha, share, role }); // shares are per role, so the label says of what
    }
  }
  const found = entries.length ? entries : ((line.visual && line.visual.items) || []).map((hex) => ({ hex }));
  if (!found.length) return [];
  return [samples("swatches", found, ({ hex, alpha, share, role }) => {
    // The chip sits inset on a mid-grey mat, so a colour close to this page's own still has an edge.
    // A see-through colour gets a checkered mat instead.
    const chip = el("span", { class: "chip" });
    chip.style.backgroundColor = alpha ? hex + Math.round(alpha * 2.55).toString(16).padStart(2, "0") : hex;
    const labels = [unit(alpha ? `${hex} · ${alpha}% opacity` : hex)];
    if (share) {
      const fill = el("span", { class: "share-fill" });
      fill.style.width = `${share.startsWith("<") ? 0.5 : parseFloat(share)}%`;
      labels.push(el("span", { class: "share", "aria-hidden": "true" }, fill), unit(`${share} of ${role}`, true));
    }
    return [el("span", { class: alpha ? "swatch clear" : "swatch" }, chip), ...labels];
  })];
}

function accentSpecimen(line) {
  const share = parseFloat(line.detail);
  if (Number.isNaN(share)) return [];
  if (share === 0) {
    return [el("p", { class: "value", text: "No accent" }),
      el("p", { class: "note", text: "No saturated colour in the captured view: the page is monochrome." })];
  }
  const fill = el("span", { class: "meter-fill" });
  fill.style.width = `${Math.min(share, 100)}%`;
  return [samples("meter", [0], () => [el("span", { class: "meter" }, fill), unit(line.detail)])];
}

function radiusSpecimen(line) {
  const radii = line.visual ? line.visual.items : pxValues(line.detail);
  if (!radii.length) return [];
  // Every pill radius draws the same shape, so they share one sample.
  const pills = radii.filter((radius) => radius >= PILL);
  const small = radii.filter((radius) => radius < PILL);
  const k = small.length ? Math.min(4, CORNER / Math.max(...small, 1)) : 1;
  return [samples("radii", [...(pills.length ? [pills] : []), ...small], (radius) => {
    if (radius === pills) return [el("span", { class: "corner pill" }), unit("Pill"), unit(`Fully round ends (set as ${pills.map(px).join(", ")})`, true)];
    const corner = el("span", { class: "corner" });
    corner.style.borderRadius = `${radius * k}px`;
    return [corner, unit(px(radius))];
  }), scaleNote(k, "Boxes")];
}

function spacingSpecimen(line) {
  const gaps = pxValues(line.detail);
  if (!gaps.length) return [];
  const widest = Math.max(...gaps);
  return [samples("rows gaps", gaps, (gap) => {
    const bar = el("span", { class: "bar" });
    bar.style.setProperty("--share", gap / widest);
    return [bar, unit(px(gap))];
  }), el("p", { class: "note", text: "Each bar is one gap. The widest is drawn at full length and the rest in proportion to it." })];
}

function containerSpecimen(line) {
  const [width] = pxValues(line.detail);
  if (!width) return [];
  return [el("div", { class: "ruler", "data-px": width },
    el("span", { class: "ruler-track", "aria-hidden": "true" }, el("span", { class: "ruler-span" })),
    el("span", { class: "ruler-ends unit quiet", "aria-hidden": "true" }, el("span", { text: "0" }), el("span")),
    unit(px(width)), unit("", true))];
}

// Once the screenshot's width is known, each ruler shows its container against the captured viewport.
// ponytail: reads the viewport width off the screenshot (captured at 1x); send it in the payload if that changes.
function scaleRulers(root, viewportWidth) {
  for (const ruler of root.querySelectorAll(".ruler")) {
    const share = Number(ruler.dataset.px) / viewportWidth;
    ruler.style.setProperty("--share", Math.min(1, share));
    ruler.style.setProperty("--tick", 100 / viewportWidth);
    ruler.querySelector(".ruler-ends").lastChild.textContent = px(viewportWidth);
    ruler.lastChild.textContent = `${Math.round(share * 100)}% of the ${viewportWidth}px captured viewport`;
  }
}

function fontsSpecimen(line) {
  const names = line.detail.split(",").map((name) => name.trim()).filter(Boolean);
  // ponytail: a family counts as monospace by its name; the payload carries no generic family.
  return [samples("fonts", names, (name) => {
    const mono = /mono|code|courier|consol|menlo|terminal/i.test(name);
    return [el("span", { class: mono ? "face mono" : "face", text: name }),
      unit(mono ? "Monospace: drawn in this screen's monospace" : "Drawn in this screen's sans-serif", true)];
  }), el("p", { class: "note", text: "Names as the page declares them. Piggyback does not load the typefaces." })];
}

const SPECIMENS = {
  type_scale: typeSpecimen, palette: paletteSpecimen, accent: accentSpecimen, radius: radiusSpecimen,
  spacing: spacingSpecimen, container: containerSpecimen, fonts: fontsSpecimen,
};

function specimen(line) {
  const draw = SPECIMENS[line.concept];
  const nodes = draw && line.detail && !/^not measured/i.test(line.detail) ? draw(line) : [];
  return nodes.length ? nodes : [el("p", { class: "value", text: line.detail || "Not measured" })];
}

// Result views

function renderResult(data) {
  state.urls.forEach((url) => URL.revokeObjectURL(url));
  state.urls = [];
  frames.clear();
  const views = { brief: briefView, compare: compareView, defaults: defaultsView };
  $("result").replaceChildren(views[data.state](data));
}

function head(kicker, title, sub) {
  return el("header", { class: "result-head" }, el("p", { class: "kicker", text: kicker }),
    el("h2", { class: "title", text: title }), sub ? el("p", { class: "sub", text: sub }) : null);
}

// One sheet row: the concept and why it matters on the left, what was measured on the right.
// A filled number is pinned on a screenshot; a hollow mark has no pin.
function specRow(line, body, mark, pinned) {
  const [title, gloss] = concept(line.name);
  return el("li", { class: "spec", "data-pin": pinned ? mark : false, tabindex: pinned ? "0" : false },
    el("div", { class: "spec-label" },
      mark ? el("span", { class: pinned ? "pin" : "pin hollow", "aria-hidden": pinned || Number(mark) ? false : "true",
        text: String(mark) }) : null,
      el("div", { class: "spec-text" }, el("h3", { text: title }),
        gloss ? el("p", { class: "gloss", text: gloss }) : null,
        line.why ? el("p", { class: "why" }, el("span", { class: "why-label", text: "Why it matters" }),
          el("span", { text: line.why })) : null)),
    el("div", { class: "spec-body" }, ...body));
}

function delta(line) {
  if (!line.before) return el("p", { class: "value", text: line.detail });
  const side = (kind, label, value) => el("p", { class: `side ${kind}` },
    el("span", { class: "side-label", text: label }), el("span", { class: "side-value", text: value }));
  return el("div", { class: "delta" }, side("before", "Your build", line.before),
    el("div", { class: "to" }, el("span", { class: "arrow", "aria-hidden": "true", text: "→" }),
      side("after", "Reference", line.after)));
}

function briefView(data) {
  return el("div", { class: "result-body" }, ...data.references.map((ref) => {
    const fonts = ref.lines.filter((line) => line.concept === "fonts"); // typefaces read with the sizes they are set in
    const lines = ref.lines.filter((line) => line.concept !== "fonts");
    lines.splice(lines.findIndex((line) => line.concept === "type_scale") + 1, 0, ...fonts);
    const figure = shotFigure("Reference", ref, [], false);
    const section = el("section", { class: "report" },
      el("div", { class: "report-head" }, figure,
        head("Design brief", bare(ref.url), `${count(ref.lines.length, "property", "properties")} measured`)),
      el("ul", { class: "sheet" }, ...lines.map((line) => specRow(line, specimen(line)))));
    figure.querySelector("img").addEventListener("load", (event) => scaleRulers(section, event.target.naturalWidth));
    return section;
  }));
}

// Each generic default the build is checked for: [name (what it is), what to do instead, the parts of its drawing].
// ponytail: the payload names only the defaults it found and carries no advice, so the full list lives here.
// Move it to glossary.py (and send every check) when BRIEF.md should say the same.
const DEFAULTS = {
  gradient_text: ["Gradient text (words filled with a colour blend)",
    "Fill the words with one solid colour. If one word needs weight, give only that word the accent.", ["Heading"]],
  one_font_flat_scale: ["Flat scale (one font, with headings barely bigger than body text)",
    "Make the main heading at least twice the body size, or set headings in a second typeface.", ["Heading", "Body text"]],
  uniform_radius: ["Uniform radius (every rounded box has the same corners)",
    "Give each kind of box its own corner: small on buttons and inputs, larger on cards.", ["", "", ""]],
  repeated_motion: ["Repeated motion (the same fade-and-move on many blocks)",
    "Keep the entrance for one or two key blocks. Let the rest appear without moving.", ["", "", ""]],
  icon_tile: ["Icon tile (an icon on a tinted square)",
    "Show the icon on its own, without the square, or replace it with a number or a real image.", ["★"]],
};

const art = (key) => el("span", { class: `art ${key}`, "aria-hidden": "true" },
  ...((DEFAULTS[key] || [])[2] || []).map((text) => el("span", { text })));
const field = (label, text, strong) => el("p", { class: strong ? "field strong" : "field" },
  el("span", { class: "field-label", text: label }), el("span", { text }));
const plural = (text) => text.replace(/\b(\d+) ([^()]*?\w)\(s\)/g, (all, n, word) => count(Number(n), word)); // "1 icon(s)" -> "1 icon"

// The defaults found come first, each with what to do instead; the checks the page is clear of share the last row.
function defaultsView(data) {
  const found = data.items.length;
  const hit = new Set(data.items.map((item) => item.concept));
  const clear = Object.keys(DEFAULTS).filter((key) => !hit.has(key));
  const rows = data.items.map((item) => specRow(item, [el("div", { class: "tell" }, art(item.concept),
    field("Found on this page", cap(plural(item.detail)), true),
    DEFAULTS[item.concept] ? field("Do instead", DEFAULTS[item.concept][1]) : null)], item.number, false));
  const cleared = clear.length ? specRow({ name: `Checked and clear (${count(clear.length, "default")} looked for, not found on this page)` },
    clear.map((key) => el("div", { class: "tell clear" }, art(key), field(...concept(DEFAULTS[key][0])),
      field("Result", "Not found"))), "✓", false) : null;
  const title = head("Defaults check", found ? `${count(found, "generic default")} detected` : "No generic defaults detected",
    `${bare(data.build.url)} · ${count(found + clear.length, "check")} run, ${found} found`);
  if (found) title.append(el("p", { class: "note", text: "A default describes the whole page, so none is pinned on the screenshot." }));
  return el("section", { class: "report" },
    el("div", { class: "report-head" }, shotFigure("Your build", data.build, [], false), title),
    el("ol", { class: "sheet", "aria-label": "Defaults checked" }, ...rows, cleared));
}

const OFF = {
  below: "below the captured view", outside: "outside the captured view", partly: "partly visible", none: "no position",
};

// A box gets a pin only when all of it is inside the captured view. Nothing is drawn clamped to an edge.
function placement(box, viewport) {
  if (!box || !viewport) return "none";
  const right = box.x + box.width;
  const bottom = box.y + box.height;
  if (box.x >= 0 && box.y >= 0 && right <= viewport.width && bottom <= viewport.height) return "in";
  if (box.y >= viewport.height) return "below";
  if (bottom <= 0 || right <= 0 || box.x >= viewport.width) return "outside";
  return "partly";
}

function compareView(data) {
  const sides = [["Reference", data.reference, "ref_box"], ["Your build", data.build, "build_box"]];
  const where = (pin) => sides.map(([, view, key]) => placement(pin[key], view.viewport));
  const rows = data.pins.map((pin) => {
    const spots = where(pin);
    const tags = spots.every((spot) => spot === "none")
      ? ["Page-level: no pin"]
      : sides.flatMap(([label], index) =>
        (spots[index] === "in" ? [] : [`No pin on ${label.toLowerCase()}: ${OFF[spots[index]]}`]));
    return specRow(pin, [delta(pin),
      tags.length ? el("ul", { class: "tags" }, ...tags.map((text) => el("li", { class: "tag", text }))) : null,
    ], pin.number, spots.includes("in"));
  });
  const ticked = (data.ticked || []).map((pin) => {
    const row = specRow({ ...pin, name: `Fixed on re-run: ${concept(pin.name)[0]}`, why: null }, [delta(pin)], "✓", false);
    row.classList.add("done");
    return row;
  });
  const missing = data.not_found.length;
  const notFound = missing
    ? specRow({ name: `Not found (${count(missing, "item")} missing on one page, so there is nothing to compare)` },
      [el("ul", {}, ...data.not_found.map((text) => el("li", { text })))], "–", false)
    : null;
  if (notFound) notFound.classList.add("notfound");
  const figures = sides.map(([label, view, key], index) => shotFigure(label, view,
    data.pins.filter((pin) => where(pin)[index] === "in").map((pin) => ({ number: pin.number, box: pin[key] })), true));
  const open = data.pins.length;
  const note = ["A filled number is pinned where the whole box is inside the captured view.",
    "A tag says why a screenshot has no pin; page-level entries have no position at all.", data.note]
    .filter(Boolean).join(" ");
  return el("div", { class: "comparison" },
    head("Comparison", open ? count(open, "difference") : "No differences found",
      `${bare(data.build.url)} against ${bare(data.reference.url)}. Values read your build → reference.`),
    el("div", { class: "compare" }, ...figures),
    open ? el("p", { class: "note", text: note }) : null,
    el("ol", { class: "sheet", "aria-label": "Differences" }, ...rows, ...ticked, notFound));
}

// Screenshots and numbered pins

// Badges sit in the rail beside the screenshot, so a badge can never cover the page. Each one is level with
// its box; boxes that stack push their badges apart, and a leader line joins every badge to its outline.
function placeMarks(img, viewport, pinMarks, nodes) {
  if (!img.naturalWidth || !img.clientWidth) return;
  const scale = img.clientWidth / viewport.width;
  const left = img.offsetLeft;
  const size = nodes[0].badge.offsetHeight;
  const rows = pinMarks
    .map(({ box }, index) => {
      const x = Math.max(0, box.x * scale - RING);
      const y = Math.max(0, box.y * scale - RING);
      const right = Math.min(img.clientWidth, (box.x + box.width) * scale + RING);
      const bottom = Math.min(img.clientHeight, (box.y + box.height) * scale + RING);
      return { ...nodes[index], x: left + x, y, w: right - x, h: bottom - y };
    })
    .sort((a, b) => a.y + a.h / 2 - (b.y + b.h / 2));
  let floor = 0;
  for (const row of rows) {
    row.top = Math.max(floor, row.y + row.h / 2 - size / 2);
    floor = row.top + size + GAP;
  }
  // ponytail: more badges than the rail is tall would run off its top; add a second column if pins ever get that many.
  let ceiling = img.clientHeight - size;
  for (const row of [...rows].reverse()) {
    row.top = Math.min(row.top, ceiling);
    ceiling = row.top - size - GAP;
  }
  for (const row of rows) {
    Object.assign(row.outline.style, { left: `${row.x}px`, top: `${row.y}px`, width: `${row.w}px`, height: `${row.h}px` });
    const fromX = (left + row.badge.offsetWidth) / 2;
    const fromY = row.top + size / 2;
    Object.assign(row.badge.style, { left: `${fromX - row.badge.offsetWidth}px`, top: `${row.top}px` });
    const inset = Math.min(3, row.h / 2);
    const toY = Math.max(row.y + inset, Math.min(fromY, row.y + row.h - inset));
    Object.assign(row.leader.style, {
      left: `${fromX}px`, top: `${fromY}px`, width: `${Math.hypot(row.x - fromX, toY - fromY)}px`,
      transform: `rotate(${Math.atan2(toY - fromY, row.x - fromX)}rad)`,
    });
  }
}

function relayoutAll() {
  for (const [frame, relayout] of frames) {
    if (frame.isConnected) relayout();
    else frames.delete(frame);
  }
}

// rail: keep the badge strip even with no pins, so two screenshots side by side stay the same size.
function shotFigure(label, view, pinMarks, rail, zoomed) {
  const { url, shot, viewport } = view;
  const size = viewport || {};
  const img = el("img", { alt: `Screenshot of ${url}`, width: size.width || false, height: size.height || false });
  const frame = el("div", { class: rail ? "frame railed" : "frame" }, img);
  const nodes = pinMarks.map(({ number }) => ({
    outline: el("div", { class: "box", "data-pin": number }),
    leader: el("div", { class: "leader", "data-pin": number }),
    badge: el("span", { class: "pin", "data-pin": number, text: String(number) }),
  }));
  frame.append(...nodes.flatMap((node) => [node.outline, node.leader, node.badge]));
  if (nodes.length) {
    const relayout = () => placeMarks(img, viewport, pinMarks, nodes);
    frames.set(frame, relayout);
    img.addEventListener("load", relayout);
  }
  shotURL(shot).then(
    (src) => { img.src = src; },
    () => frame.replaceChildren(el("p", { class: "muted", text: "Screenshot could not be loaded." })),
  );
  if (zoomed) return el("figure", { class: "shot" }, frame);
  const full = el("button", { type: "button", class: "btn small", text: "Full size" });
  full.addEventListener("click", () => {
    $("zoom-title").textContent = `${label} · ${bare(url)}`;
    $("zoom-body").replaceChildren(shotFigure(label, view, pinMarks, rail, true));
    $("zoom").showModal();
  });
  return el("figure", { class: "shot" },
    el("figcaption", { class: "shot-cap" }, el("span", { class: "shot-label", text: label }),
      el("span", { class: "shot-url", text: bare(url) }), full),
    frame);
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
