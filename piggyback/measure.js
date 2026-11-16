// Page-level design fingerprint, evaluated in the page by measure.py. Returns plain JSON.
// Captured strings (heading text, class names) are returned as data and never evaluated.
() => {
  const VW = window.innerWidth;
  const VH = window.innerHeight;
  const round = (n) => Math.round(n * 100) / 100;
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
  // ponytail: overlapping boxes double count; the share is capped at 1.
  const accent = [...accents.values()].sort((a, b) => b.sat - a.sat)[0];
  const palette = {
    text: hist(texts.map((b) => [b.cs.color, b.area])),
    background: hist(bgs.map((b) => [b.cs.backgroundColor, b.area])),
    accent_share: accent ? round(Math.min(1, accent.area / (VW * VH))) : 0,
  };

  const shapes = boxes.filter((b) => b.area > 0);
  const radius = hist(
    shapes
      .filter((b) => b.cs.borderTopLeftRadius !== "0px")
      .map((b) => [b.cs.borderTopLeftRadius, b.area]),
  );
  const shadow = hist(
    shapes.filter((b) => b.cs.boxShadow !== "none").map((b) => [b.cs.boxShadow, b.area]),
  );

  // Spacing: vertical gaps between children that overlap horizontally (stacked), plus padding.
  const gaps = [];
  const padding = [];
  let container = null;
  for (const b of boxes) {
    for (const side of ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft"]) {
      if (b.cs[side] !== "0px") padding.push([b.cs[side], 1]);
    }
    const kids = [...b.el.children].map((c) => byEl.get(c)).filter(Boolean);
    if (kids.length < 2) continue;
    if (b.r.width < VW && (container === null || b.r.width > container)) container = b.r.width;
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
    const key = JSON.stringify([kind || "waapi", t.duration, t.delay, t.easing]);
    motionCounts.set(key, (motionCounts.get(key) || 0) + 1);
  }
  const motion = [...motionCounts]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map(([key, count]) => {
      const [kind, duration, delay, easing] = JSON.parse(key);
      return { kind, duration, delay, easing, count };
    });

  // Anchors. Each is null when the page has no such element; never guessed.
  const describe = (b) =>
    b && {
      tag: b.tag.toLowerCase(),
      text: textOf(b.el).slice(0, 80),
      font_size: b.cs.fontSize,
      line_height: b.cs.lineHeight,
      letter_spacing: b.cs.letterSpacing,
      font_weight: b.cs.fontWeight,
      font_family: b.cs.fontFamily,
      color: b.cs.color,
      background: b.cs.backgroundColor,
      radius: b.cs.borderTopLeftRadius,
      padding: [b.cs.paddingTop, b.cs.paddingRight, b.cs.paddingBottom, b.cs.paddingLeft],
      box: {
        x: Math.round(b.r.left),
        y: Math.round(b.r.top),
        width: Math.round(b.r.width),
        height: Math.round(b.r.height),
      },
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
  let repeated = null;
  for (const siblings of groups.values()) {
    for (const list of siblings.values()) {
      if (list.length >= 3 && (repeated === null || list.length > repeated.length)) {
        repeated = list;
      }
    }
  }

  return {
    viewport: { width: VW, height: VH },
    type,
    palette,
    radius,
    shadow,
    spacing,
    container_width: container === null ? null : round(container),
    motion,
    anchors: {
      heading: describe(heading),
      body: describe(body),
      cta: describe(cta),
      nav: describe(nav),
      card: repeated === null ? null : { count: repeated.length, ...describe(repeated[0]) },
    },
  };
};
