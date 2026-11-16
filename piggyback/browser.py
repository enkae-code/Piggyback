"""Sandboxed Chromium launch: never --no-sandbox; Playwright gives each launch a fresh profile."""

# Installed Chrome first, then Playwright's bundled Chromium (None = default channel).
CHANNELS = ("chrome", None)


class BrowserUnavailable(RuntimeError):
    pass


def launch(playwright):
    """Return (browser, channel label)."""
    errors = []
    for channel in CHANNELS:
        try:
            return playwright.chromium.launch(channel=channel), channel or "bundled chromium"
        except Exception as exc:  # Playwright raises its own error types; keep the first line.
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
            errors.append(f"{channel or 'bundled chromium'}: {first}")
    raise BrowserUnavailable("; ".join(errors))
