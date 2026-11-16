"""Plain words for each concept the brief, pins and defaults name. Shared by all three."""

TERMS = {
    "type_scale": (
        "Type scale (the set of text sizes a page uses)",
        "Clear steps between sizes make headings feel deliberate.",
    ),
    "tracking": (
        "Tracking (the space between letters)",
        "Tight tracking makes big headings feel deliberate.",
    ),
    "palette": (
        "Palette (the colours used for text and backgrounds)",
        "A few colours used consistently make a page look chosen.",
    ),
    "accent": (
        "Accent (the saturated colour that draws the eye)",
        "A small share keeps it for emphasis; a large share competes with the content. "
        "Hallmark's line (not Piggyback's verdict): past about 5% of a viewport it warns, "
        "past 8% it fails.",
    ),
    "radius": (
        "Radius (how rounded the corners of a box are)",
        "One radius across boxes makes their shapes feel related.",
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
        "Fonts (the typefaces named on the page)",
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
        "Menu labels at a consistent size keep the navigation calm.",
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
        "Only one corner shape across cards, buttons and inputs reads as default.",
    ),
    "repeated_motion": (
        "Repeated motion (the same fade-and-move on many blocks)",
        "Motion on every block competes with the content; a few deliberate moments read as chosen.",
    ),
    "icon_tile": (
        "Icon tile (an icon on a tinted square)",
        "A common default; a bare icon often reads cleaner.",
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
