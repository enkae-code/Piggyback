"""Sandboxed Chromium launch: never --no-sandbox; Playwright gives each launch a fresh profile.

Playwright adds --no-sandbox unless chromium_sandbox=True, so it is passed on every launch.
"""

# Installed Chrome first, then Playwright's bundled Chromium (None = default channel).
CHANNELS = ("chrome", None)
# WebRTC sends UDP straight from the browser, around the proxy. Two layers stop it: this switch
# (honoured by Playwright's bundled headless shell, measured: 12 datagrams to a LAN host without
# it, 0 with it; the non-"force" spelling is ignored) and NO_WEBRTC, because full Chrome and the
# "chromium" channel ignore the switch.
ARGS = ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
# Context init script: runs in every frame and popup before page code, so pages cannot reach the
# constructors through a fresh iframe either.
NO_WEBRTC = "delete window.RTCPeerConnection; delete window.webkitRTCPeerConnection;"


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
