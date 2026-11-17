"""Plain words for each concept the brief, pins and defaults name. Shared by all three."""

# Concepts re-expressed in our own words from pbakaus/impeccable (Apache-2.0)
# and Leonxlnx/taste-skill (MIT); no source wording is copied.
TERMS = {
    "type_scale": (
        "Type scale (the set of text sizes used in the first screen)",
        "Clear steps between sizes make headings feel deliberate.",
    ),
    "palette": (
        "Palette (the colours used for text and backgrounds)",
        "A few colours used consistently make a page look chosen.",
    ),
    # The 5% and 8% thresholds are Hallmark's (MIT, github.com/Nutlope/hallmark), not ours.
    "accent": (
        "Accent (the saturated colour that draws the eye)",
        "A small share keeps it for emphasis; a large share competes with the content. "
        "Hallmark's line (not Piggyback's verdict): accent past about 5% of a viewport is a "
        "reflex unless the design declares that posture; its render check fails past 8%.",
    ),
    "radius": (
        "Radius (how rounded the corners of a box are)",
        "Corner radius is one of the shape cues a reader sees across boxes.",
    ),
    "spacing": (
        "Spacing rhythm (the gaps and padding between blocks)",
        "Repeated gaps make a layout easy to scan.",
    ),
    "container": (
        "Container width (how wide the main content column is)",
        "A narrower column keeps long lines comfortable to read.",
    ),
    "fonts": (
        "Fonts (the typefaces used for text in the first screen)",
        "The typeface sets the page's voice, so matching it matters as much as matching sizes.",
    ),
    "heading_type": (
        "Heading type (size and line spacing of the main heading)",
        "A heading's size sets the top step of the hierarchy.",
    ),
    "body_type": (
        "Body text type (size and line spacing of running text)",
        "Comfortable line spacing keeps longer text easy to follow.",
    ),
    "button_type": (
        "Button label (the text on the main action)",
        "A clear label on the main action makes the next step obvious.",
    ),
    "nav_type": (
        "Navigation type (the menu labels)",
        "Menu labels set the type of the navigation.",
    ),
    "card_type": (
        "Repeated card type (the text in repeated boxes)",
        "Repeated boxes with matching type read as one system.",
    ),
    "gradient_text": (
        "Gradient text (words filled with a colour blend)",
        "A common default; the colour blend pulls attention away from the words.",
    ),
    "one_font_flat_scale": (
        "Flat scale (one font, with headings barely bigger than body text)",
        "Without size contrast, the hierarchy has to come from somewhere else.",
    ),
    "uniform_radius": (
        "Uniform radius (every rounded box has the same corners)",
        "Cards, buttons and inputs in the first screen share one corner radius.",
    ),
    "motion": (
        "Motion (the animations and transitions measured in the whole document)",
        "Each signature records its kind, timing, easing, animated properties, and element count.",
    ),
    "repeated_motion": (
        "Repeated motion (the same fade-and-move on many blocks)",
        "Motion on every block competes with the content; a few deliberate moments read as chosen.",
    ),
    "icon_tile": (
        "Icon tile (an icon on a tinted square)",
        "Counted, not rated: an icon on a tinted square is one pattern among several.",
    ),
}

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def note(concept, detail, number=None, why=True):
    """Markdown: the concept with its detail, then (when asked) why it matters."""
    plain, reason = TERMS[concept]
    mark = ""
    if number is not None:
        mark = (CIRCLED[number - 1] if number <= len(CIRCLED) else f"({number})") + " "
    line = f"- {mark}{plain}: {detail}"
    return f"{line}\n  - Why it matters: {reason}" if why else line
