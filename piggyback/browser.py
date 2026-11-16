"""Sandboxed Chromium launch: never --no-sandbox; Playwright gives each launch a fresh profile.

Playwright adds --no-sandbox unless chromium_sandbox=True, so it is passed on every launch.
"""

# Installed Chrome first, then Playwright's bundled Chromium (None = default channel).
CHANNELS = ("chrome", None)
# WebRTC sends UDP straight from the browser, around the proxy. This policy allows UDP only
# through a proxy, so measured pages cannot reach LAN hosts over STUN/TURN. The spelling is
# --webrtc-ip-handling-policy: --force-webrtc-ip-handling-policy is ignored by current Chromium.
ARGS = ["--webrtc-ip-handling-policy=disable_non_proxied_udp"]


class BrowserUnavailable(RuntimeError):
    pass


def launch(playwright, **options):
    """Return (browser, channel label). Extra options (e.g. proxy) go to chromium.launch."""
    errors = []
    for channel in CHANNELS:
        try:
            browser = playwright.chromium.launch(
                channel=channel, chromium_sandbox=True, args=ARGS, **options
            )
            return browser, channel or "bundled chromium"
        except Exception as exc:  # Playwright raises its own error types; keep the first line.
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
            errors.append(f"{channel or 'bundled chromium'}: {first}")
    raise BrowserUnavailable("; ".join(errors))
