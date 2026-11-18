// Page-level design fingerprint, evaluated in the page by measure.py. Returns plain JSON.
// Captured strings (heading text, class names) are returned as data and never evaluated.
() => {
  // clientWidth leaves out a visible scrollbar, so centring matches where the layout puts content.
  // ponytail: untested in CI, headless Chromium never lays out scrollbars (innerWidth == clientWidth).
  const VW = document.documentElement.clientWidth;
  const VH = window.innerHeight;
  const round = (n) => Math.round(n * 100) / 100;
  const boxOf = (r) => ({
    x: Math.round(r.left),
    y: Math.round(r.top),
    width: Math.round(r.width),
    height: Math.round(r.height),
  });
  // The first rendered element in document order; null when none is rendered.
  const firstBox = (els) => {
    let first = null;
    for (const el of els) {
      const r = el instanceof Element ? el.getBoundingClientRect() : null;
      if (!r || r.width <= 0 || r.height <= 0) continue;
      if (first === null || (first.el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING)) {
        first = { el, r };
      }
    }
    return first && boxOf(first.r);
  };
  const rgba = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return p.length >= 3 ? [p[0], p[1], p[2], p.length > 3 ? p[3] : 1] : null;
  };
  const hasBg = (b) => {
    const c = rgba(b.cs.backgroundColor);
    return c !== null && c[3] > 0;
  };
  const clipArea = (r) => {
    const w = Math.min(r.right, VW) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, VH) - Math.max(r.top, 0);
    return w > 0 && h > 0 ? w * h : 0;
  };
  const hist = (pairs, top = 8) => {
    const totals = new Map();
    let sum = 0;
    for (const [value, weight] of pairs) {
      if (weight <= 0) continue;
      totals.set(value, (totals.get(value) || 0) + weight);
      sum += weight;
    }
    return [...totals]
      .sort((a, b) => b[1] - a[1])
      .slice(0, top)
      .map(([value, w]) => ({ value, share: round(w / sum) }));
  };
  const textOf = (el) => (el.innerText || "").trim();
  // Text a reader sees: visually-hidden copies (1px boxes) are skipped; innerText keeps them.
  const seenText = (el) => {
    let out = "";
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) out += node.textContent;
      else if (node.nodeType === Node.ELEMENT_NODE) {
        const r = node.getBoundingClientRect();
        if (r.width > 1 && r.height > 1) out += ` ${seenText(node)} `;
      }
    }
    return out;
  };
  const best = (list, score) =>
    list.reduce((top, b) => (top === null || score(b) > score(top) ? b : top), null);

  const boxes = [];
  for (const el of document.body ? document.body.querySelectorAll("*") : []) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (
      cs.display === "none" ||
      cs.visibility === "hidden" ||
      cs.opacity === "0" ||
      r.width <= 0 ||
      r.height <= 0
    ) {
      continue;
    }
    boxes.push({ el, cs, r, tag: el.tagName, area: clipArea(r) });
  }
  const byEl = new Map(boxes.map((b) => [b.el, b]));

  // Type: text elements, weighted by their visible area.
  const texts = boxes.filter((b) => b.area > 0 && textOf(b.el) !== "");
  const type = {
    font_size: hist(texts.map((b) => [b.cs.fontSize, b.area])),
    line_height: hist(texts.map((b) => [b.cs.lineHeight, b.area])),
    letter_spacing: hist(texts.map((b) => [b.cs.letterSpacing, b.area])),
    font_weight: hist(texts.map((b) => [b.cs.fontWeight, b.area])),
    families: hist(texts.map((b) => [b.cs.fontFamily, b.area])),
  };

  // Palette: background colours by area; accent = most saturated non-neutral background.
  const SATURATED = 0.25;
  const sat = ([r, g, b]) => {
    const max = Math.max(r, g, b);
    return max === 0 ? 0 : (max - Math.min(r, g, b)) / max;
  };
  const bgs = boxes.filter((b) => b.area > 0 && hasBg(b));
  const accents = new Map();
  for (const b of bgs) {
    const s = sat(rgba(b.cs.backgroundColor));
    if (s < SATURATED) continue;
    const entry = accents.get(b.cs.backgroundColor) || { sat: s, area: 0 };
    entry.area += b.area;
    accents.set(b.cs.backgroundColor, entry);
  }
  // ponytail: overlapping boxes double count; the share is capped at 1. The canvas is one more
  // overlap: html and body are not boxes, so the canvas (html's background, else body's, as CSS
  // propagates it) is counted once at viewport weight. Accent ignores it.
  const canvas = [document.documentElement, document.body]
    .filter(Boolean)
    .map((el) => getComputedStyle(el).backgroundColor)
    .find((c) => rgba(c)?.[3] > 0);
  const accent = [...accents.values()].sort((a, b) => b.sat - a.sat)[0];
  const palette = {
    text: hist(texts.map((b) => [b.cs.color, b.area])),
    background: hist([
      ...bgs.map((b) => [b.cs.backgroundColor, b.area]),
      ...(canvas ? [[canvas, VW * VH]] : []),
    ]),
    accent_share: accent ? round(Math.min(1, accent.area / (VW * VH))) : 0,
  };

  const shapes = boxes.filter((b) => b.area > 0);
  const rounded = shapes.filter((b) => b.cs.borderTopLeftRadius !== "0px");
  const radius = hist(rounded.map((b) => [b.cs.borderTopLeftRadius, b.area]));
  const shadow = hist(
    shapes.filter((b) => b.cs.boxShadow !== "none").map((b) => [b.cs.boxShadow, b.area]),
  );

  // Spacing: vertical gaps between children that overlap horizontally (stacked), plus padding.
  const gaps = [];
  const padding = [];
  for (const b of boxes) {
    for (const side of ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft"]) {
      if (b.cs[side] !== "0px") padding.push([b.cs[side], 1]);
    }
    const kids = [...b.el.children].map((c) => byEl.get(c)).filter(Boolean);
    if (kids.length < 2) continue;
    kids.sort((x, y) => x.r.top - y.r.top);
    for (let i = 1; i < kids.length; i++) {
      const prev = kids[i - 1].r;
      const next = kids[i].r;
      const stacked = next.left < prev.right && prev.left < next.right;
      const gap = next.top - prev.bottom;
      if (stacked && gap >= 0) gaps.push([Math.round(gap), 1]);
    }
  }
  const spacing = { gaps: hist(gaps), padding: hist(padding) };

  // Motion: CSS transitions/animations and WAAPI, by timing signature.
  const motionCounts = new Map();
  for (const a of document.getAnimations()) {
    const t = a.effect ? a.effect.getComputedTiming() : {};
    const kind = { CSSTransition: "transition", CSSAnimation: "animation" }[a.constructor.name];
    const frames = a.effect ? a.effect.getKeyframes() : [];
    const props = [...new Set(frames.flatMap((f) => Object.keys(f)))]
      .filter((p) => !["offset", "computedOffset", "easing", "composite"].includes(p))
      .sort();
    const key = JSON.stringify([kind || "waapi", t.duration, t.delay, t.easing, props]);
    if (!motionCounts.has(key)) motionCounts.set(key, new Set());
    motionCounts.get(key).add(a.effect?.target);
  }
  const motion = [...motionCounts]
    .sort((a, b) => b[1].size - a[1].size)
    .slice(0, 20)
    .map(([key, targets]) => {
      const [kind, duration, delay, easing, props] = JSON.parse(key);
      return { kind, duration, delay, easing, props, count: targets.size, box: firstBox(targets) };
    });

  // Effects: counts behind the defaults-detected rules.
  const gradientTexts = texts.filter(
    (b) =>
      (b.cs.backgroundClip || b.cs.webkitBackgroundClip) === "text" &&
      b.cs.backgroundImage.includes("gradient"),
  );
  // Tinted: translucent, or an opaque pastel that differs from the page background. Squares
  // only: circles (avatars) and buttons are not icon tiles.
  const paint = (cs) => {
    const c = rgba(cs.backgroundColor);
    return c && c[3] > 0 ? c : null;
  };
  const pageBg =
    paint(getComputedStyle(document.body)) ||
    paint(getComputedStyle(document.documentElement)) ||
    [255, 255, 255, 1];
  const tinted = (c) =>
    c[3] < 1 ||
    (Math.max(...c.slice(0, 3)) >= 200 &&
      sat(c) >= 0.1 &&
      Math.hypot(...c.slice(0, 3).map((v, i) => v - pageBg[i])) > 16);
  const CONTROL = "button, input, [role=button]";
  // A percentage radius is relative to the box, so compare it in pixels.
  const radiusPx = (b) => {
    const r = b.cs.borderTopLeftRadius;
    return r.endsWith("%") ? (parseFloat(r) / 100) * b.r.width : parseFloat(r);
  };
  const tintedIconSquares = boxes.filter(
    (b) =>
      hasBg(b) &&
      tinted(rgba(b.cs.backgroundColor)) &&
      Math.abs(b.r.width - b.r.height) <= 1 &&
      b.r.width >= 24 &&
      b.r.width <= 80 &&
      radiusPx(b) < b.r.width / 2 &&
      !b.el.matches(CONTROL) &&
      b.el.querySelector("svg") !== null,
  );

  // Anchors. Each is null when the page has no such element; never guessed.
  // Text anchors (heading, body) are blocks as wide as their container; their pin belongs on the
  // ink, so their box is the bounding box of their text clipped to the element (animated headings
  // park copies of their words off to the side), falling back to the element box.
  const inkRect = (b) => {
    const range = document.createRange();
    range.selectNodeContents(b.el);
    const r = range.getBoundingClientRect();
    const left = Math.max(r.left, b.r.left);
    const top = Math.max(r.top, b.r.top);
    const width = Math.min(r.right, b.r.right) - left;
    const height = Math.min(r.bottom, b.r.bottom) - top;
    return width > 0 && height > 0 ? { left, top, width, height } : b.r;
  };
  const describe = (b, ink = false) =>
    b && {
      tag: b.tag.toLowerCase(),
      text: seenText(b.el).replace(/\s+/g, " ").trim().slice(0, 80),
      font_size: b.cs.fontSize,
      line_height: b.cs.lineHeight,
      letter_spacing: b.cs.letterSpacing,
      font_weight: b.cs.fontWeight,
      font_family: b.cs.fontFamily,
      color: b.cs.color,
      background: b.cs.backgroundColor,
      radius: b.cs.borderTopLeftRadius,
      padding: [b.cs.paddingTop, b.cs.paddingRight, b.cs.paddingBottom, b.cs.paddingLeft],
      box: boxOf(ink ? inkRect(b) : b.r),
    };
  // Score keys: font size or CTA background dominates, visible area breaks ties.
  const heading = best(
    boxes.filter((b) => /^H[1-6]$/.test(b.tag) && b.area > 0 && textOf(b.el) !== ""),
    (b) => parseFloat(b.cs.fontSize) * 1e9 + b.area,
  );
  const body = boxes.find((b) => b.tag === "P" && b.area > 0 && textOf(b.el).length >= 40);
  const ctaSelector =
    'button, input[type=button], input[type=submit], a[role=button], a[class*="btn" i], a[class*="button" i]';
  const cta = best(
    boxes.filter((b) => b.area > 0 && b.el.matches(ctaSelector)),
    (b) => (hasBg(b) ? 1e9 : 0) + b.area,
  );
  const nav = boxes.find((b) => b.area > 0 && b.el.matches('nav, [role="navigation"]'));

  // Most-repeated sibling: same tag and class under one parent, at least 3 of them.
  const groups = new Map();
  for (const b of boxes) {
    const cls = b.el.getAttribute("class");
    if (!cls || !b.el.parentElement) continue;
    if (!groups.has(b.el.parentElement)) groups.set(b.el.parentElement, new Map());
    const siblings = groups.get(b.el.parentElement);
    const sig = `${b.tag} ${cls}`;
    if (!siblings.has(sig)) siblings.set(sig, []);
    siblings.get(sig).push(b);
  }
  // A card: a box with text or child elements, at least 120x80, fully inside the viewport (an
  // off-screen carousel slide is not on the page), never a control on its own and never part of
  // an SVG drawing (repeated <g> groups are artwork, not cards).
  const isCard = (b) =>
    b.r.width >= 120 &&
    b.r.height >= 80 &&
    b.r.left >= 0 &&
    b.r.right <= VW &&
    (textOf(b.el) !== "" || b.el.children.length > 0) &&
    !b.el.matches(CONTROL) &&
    !(b.el instanceof SVGElement);
  let repeated = null;
  for (const siblings of groups.values()) {
    for (const list of siblings.values()) {
      const cards = list.filter(isCard);
      if (cards.length >= 3 && (repeated === null || cards.length > repeated.length)) {
        repeated = cards;
      }
    }
  }

  // Page column: a box that is centred and narrower than the viewport, 100px tall or more.
  // Widths are content widths (minus horizontal padding); each one weighs its visible area and the
  // heaviest wins, widest on a tie. Null when no box qualifies: never a guess. Body text measure
  // is not used: a narrow text block would otherwise stand in for the page column.
  const COLUMN_MIN_HEIGHT = 100;
  const CENTRE_TOLERANCE = 2;
  const candidates = [];
  // body itself is a common page column (example.com) but sits outside `boxes`.
  const bodyBox = document.body && {
    el: document.body,
    cs: getComputedStyle(document.body),
    r: document.body.getBoundingClientRect(),
  };
  for (const b of bodyBox ? [bodyBox, ...boxes] : boxes) {
    b.area ??= clipArea(b.r);
    const left = b.r.left;
    const right = VW - b.r.right;
    if (b.area <= 0 || b.r.height < COLUMN_MIN_HEIGHT) continue;
    if (left <= 0 || right <= 0 || Math.abs(left - right) > CENTRE_TOLERANCE) continue;
    const padding = parseFloat(b.cs.paddingLeft) + parseFloat(b.cs.paddingRight);
    candidates.push({
      width: Math.round(b.r.width - padding),
      area: b.area,
      px: /^[\d.]+px$/.test(b.cs.maxWidth),
    });
  }
  // A column with its own px max-width beats a full-width section with gutters, which can out-area it.
  const pool = candidates.some((c) => c.px) ? candidates.filter((c) => c.px) : candidates;
  const columns = new Map();
  for (const c of pool) columns.set(c.width, (columns.get(c.width) || 0) + c.area);
  let container = null;
  for (const [width, area] of columns) {
    if (
      container === null ||
      area > columns.get(container) ||
      (area === columns.get(container) && width > container)
    ) {
      container = width;
    }
  }

  return {
    viewport: { width: VW, height: VH },
    type,
    palette,
    radius,
    rounded_boxes: rounded.length,
    shadow,
    spacing,
    container_width: container,
    motion,
    effects: {
      gradient_text: gradientTexts.length,
      tinted_icon_squares: tintedIconSquares.length,
      gradient_text_box: gradientTexts.length ? boxOf(inkRect(gradientTexts[0])) : null,
      tinted_icon_square_box: tintedIconSquares.length ? boxOf(tintedIconSquares[0].r) : null,
    },
    anchors: {
      heading: describe(heading, true),
      body: describe(body, true),
      cta: describe(cta),
      nav: describe(nav),
      card: repeated === null ? null : { count: repeated.length, ...describe(repeated[0]) },
    },
  };
};
