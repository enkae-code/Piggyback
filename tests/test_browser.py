import unittest

from piggyback.browser import BrowserUnavailable, launch


class FakeChromium:
    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["channel"] in self.failing:
            raise RuntimeError(f"{kwargs['channel']} not found\nmore detail")
        return "browser"


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class LaunchTests(unittest.TestCase):
    def test_every_attempt_keeps_the_sandbox(self):
        chromium = FakeChromium(failing=("chrome",))
        browser, label = launch(FakePlaywright(chromium))
        self.assertEqual((browser, label), ("browser", "bundled chromium"))
        self.assertEqual([c["channel"] for c in chromium.calls], ["chrome", None])
        self.assertTrue(all(c["chromium_sandbox"] is True for c in chromium.calls))

    def test_options_reach_chromium(self):
        chromium = FakeChromium()
        launch(FakePlaywright(chromium), proxy={"server": "http://127.0.0.1:9"})
        self.assertEqual(chromium.calls[0]["proxy"], {"server": "http://127.0.0.1:9"})
        self.assertTrue(chromium.calls[0]["chromium_sandbox"])

    def test_webrtc_udp_is_limited_to_the_proxy(self):
        chromium = FakeChromium(failing=("chrome",))
        launch(FakePlaywright(chromium))
        for call in chromium.calls:
            self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", call["args"])

    def test_all_channels_failing_names_each(self):
        chromium = FakeChromium(failing=("chrome", None))
        with self.assertRaisesRegex(BrowserUnavailable, "chrome: chrome not found"):
            launch(FakePlaywright(chromium))
        self.assertEqual(len(chromium.calls), 2)


if __name__ == "__main__":
    unittest.main()
