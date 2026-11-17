"""Round 5: speakers and microphones listed once, stored exactly, opened at a rate they accept."""

import numpy as np
import pytest

from tau import audio
from test_settings import fake_brains, served  # noqa: F401  (fixtures)

APIS = ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"]
LONG = "Speakers (Realtek(R) High Definition Audio)"


def dev(name, api, ins=0, outs=2, rate=48000.0):
    return {"name": name, "hostapi": APIS.index(api), "max_input_channels": ins,
            "max_output_channels": outs, "default_samplerate": rate}


class FakeSD:
    """Duplicate devices across Windows host APIs; WASAPI devices reject 24 kHz and 16 kHz."""

    def __init__(self):
        self.devs = [
            dev("Microsoft Sound Mapper - Output", "MME"),
            dev(LONG[:31], "MME", rate=44100.0),                   # MME cuts names to 31 characters
            dev("Headset Earphone (USB)", "MME"),
            dev("Microphone (USB)", "MME", ins=1, outs=0),
            dev("Primary Sound Driver", "Windows DirectSound"),
            dev(LONG, "Windows DirectSound"),
            dev("Headset Earphone (USB)", "Windows DirectSound"),
            dev(LONG, "Windows WASAPI"),                           # 7
            dev("Headset Earphone (USB)", "Windows WASAPI"),       # 8
            dev("Microphone (USB)", "Windows WASAPI", ins=1, outs=0),   # 9
            dev(LONG, "Windows WDM-KS"),
            dev("Only On MME", "MME"),                             # 11: no WASAPI twin
        ]
        self.played = []
        self.broken = set()

    def query_hostapis(self):
        return [{"name": n} for n in APIS]

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return self.devs
        return self.devs[device if device is not None else 7]

    def _check(self, device, samplerate):
        d = self.devs[device if device is not None else 7]
        if APIS[d["hostapi"]] == "Windows WASAPI" and samplerate != int(d["default_samplerate"]):
            raise RuntimeError("Invalid sample rate")

    def check_output_settings(self, device=None, samplerate=None, channels=1, dtype=None):
        self._check(device, samplerate)

    def check_input_settings(self, device=None, samplerate=None, channels=1, dtype=None):
        self._check(device, samplerate)

    def play(self, data, rate, device=None):
        if device in self.broken:
            raise RuntimeError("Error opening OutputStream: Device unavailable [PaErrorCode -9985]")
        self._check(device, rate)
        self.played.append((device, rate, len(data)))

    def wait(self):
        pass

    def rec(self, frames, samplerate, channels, dtype, device=None):
        self._check(device, samplerate)
        t = np.arange(frames) / samplerate
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32").reshape(-1, 1)


def test_each_physical_device_once_with_system_default_first():
    sd = FakeSD()
    got = audio.list_devices(sd)
    labels = [o["label"] for o in got["speakers"]]
    assert labels[0] == "System default" and got["speakers"][0]["value"] == "default"
    assert labels.count(LONG) == 1 and labels.count("Headset Earphone (USB)") == 1
    assert not any(l.startswith(LONG[:31]) and l != LONG for l in labels)       # the cut MME twin is gone
    assert "Only On MME" in labels                                              # MME is the fallback
    assert not any("Sound Mapper" in l or "Primary Sound" in l for l in labels)
    values = [o["value"] for o in got["speakers"]]
    assert len(values) == len(set(values))                                      # one pick lights one row
    assert "Windows WASAPI|7|" + LONG in values                                  # WASAPI preferred
    assert [o["label"] for o in got["mics"]] == ["System default", "Microphone (USB)"]


def test_stored_by_hostapi_index_and_name_and_matched_exactly():
    sd = FakeSD()
    assert audio.resolve("Windows WASAPI|8|Headset Earphone (USB)", "output", sd) == (8, None)
    # the index moved (a device was plugged in): host API and name still find it
    assert audio.resolve("Windows WASAPI|3|Headset Earphone (USB)", "output", sd) == (8, None)
    # a plain name saved by TAU 1.1 no longer errors with "multiple devices": WASAPI wins
    assert audio.resolve("Headset Earphone (USB)", "output", sd) == (8, None)
    assert audio.resolve("default", "output", sd) == (None, None)
    idx, note = audio.resolve("Windows WASAPI|99|Gone Speaker", "output", sd)
    assert idx is None and "system default" in note


def test_opens_at_a_rate_the_device_supports_and_resamples():
    sd = FakeSD()
    assert audio.pick_rate(7, "output", 24000, sd) == 48000      # WASAPI rejects Kokoro's 24 kHz
    tone = np.zeros(24000, "float32")
    assert audio.play(tone, 24000, "Windows WASAPI|7|" + LONG, sd) is None
    assert sd.played[-1] == (7, 48000, 48000)                    # one second, resampled to 48 kHz
    rec = audio.record(1.0, 16000, "Windows WASAPI|9|Microphone (USB)", sd)
    assert len(rec) == 16000                                     # recorded at 48 kHz, handed over at 16 kHz


def test_a_failing_device_falls_back_to_the_default_and_says_so():
    sd = FakeSD()
    sd.broken.add(8)
    note = audio.play(np.zeros(2400, "float32"), 24000, "Windows WASAPI|8|Headset Earphone (USB)", sd)
    assert "Headset Earphone (USB)" in note and "system default" in note and "Device unavailable" in note
    assert sd.played[-1][0] is None


def test_streaming_resampler_keeps_length_across_blocks():
    rs = audio.Resampler(24000, 48000)
    total = sum(len(rs(np.ones(960, "float32"))) for _ in range(50))
    assert abs(total - 48000 * 50 * 960 / 24000 / 1) < 4 * 50 / 1 + 4


def test_sink_resamples_and_falls_back(monkeypatch):
    import sys
    import types

    from tau.voice import SoundDeviceSink

    sd = FakeSD()
    opened = []

    class Stream:
        def __init__(self, samplerate, channels, dtype, device):
            if device in sd.broken:
                raise RuntimeError("Device unavailable")
            sd._check(device, samplerate)
            opened.append((device, samplerate))
            self.got = 0

        def start(self):
            pass

        def write(self, data):
            self.got += len(data)

        def stop(self):
            pass

        def close(self):
            pass

    fake = types.SimpleNamespace(**{k: getattr(sd, k) for k in dir(sd) if not k.startswith("__")})
    fake.OutputStream = Stream
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    said = []
    sink = SoundDeviceSink("Windows WASAPI|7|" + LONG, on_problem=said.append)
    sink.open(24000)
    sink.write(np.zeros(960, "float32"))
    assert opened[-1] == (7, 48000) and 1900 <= sink._stream.got <= 1920 and not said
    sd.broken.add(8)
    sink.set_device("Windows WASAPI|8|Headset Earphone (USB)")
    sink.open(24000)
    assert opened[-1][0] is None and said and "system default" in said[-1]


def test_settings_lists_once_and_lights_one_row(tmp_path):
    from tau.settings import Settings

    sd = FakeSD()
    st = Settings({"audio": {"speaker": "Windows WASAPI|8|Headset Earphone (USB)"}}, tmp_path / "p.yaml",
                  audio_devices=lambda: audio.list_devices(sd))
    rows = {r["id"]: r for r in st.view("voice")["rows"]}
    spk = rows["speaker"]
    lit = [o for o in spk["options"] if o["value"] == spk["value"]]
    assert len(lit) == 1 and lit[0]["label"] == "Headset Earphone (USB)"
    assert rows["mic"]["value"] == "default"                  # nothing saved: System default is lit


def test_speaker_test_shows_played_or_the_exact_failure(tmp_path):
    from tau.settings import Settings

    st = Settings({"audio": {"speaker": "default"}}, tmp_path / "p.yaml", audio_devices=lambda: {})
    st.hooks["test_speaker"] = lambda d: True
    st.change("voice", "test_speaker", None)
    assert {r["id"]: r for r in st.view("voice")["rows"]}["speaker_result"]["value"].startswith("played")

    def boom(d):
        raise RuntimeError("Error opening OutputStream: Invalid sample rate [PaErrorCode -9997]")

    st.hooks["test_speaker"] = boom
    out = st.change("voice", "test_speaker", None)
    assert out["ok"] is False and "Invalid sample rate" in out["message"]
    assert "Invalid sample rate" in {r["id"]: r for r in st.view("voice")["rows"]}["speaker_result"]["value"]


def test_microphone_feed_resamples_to_16k():
    import threading

    from tau.ears.pipeline import FRAME, run_microphone

    sd = FakeSD()
    frames = []
    stop = threading.Event()

    class In:
        def __init__(self, samplerate, channels, dtype, blocksize, device):
            sd._check(device, samplerate)
            self.rate, self.block = samplerate, blocksize

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            if len(frames) >= 10:
                stop.set()
            return np.zeros((n, 1), "float32"), False

    class Ears:
        def feed(self, f):
            frames.append(f)

    fake = type("SD", (), {})()
    for k in ("query_devices", "query_hostapis", "check_input_settings"):
        setattr(fake, k, getattr(sd, k))
    fake.InputStream = In
    run_microphone(Ears(), "Windows WASAPI|9|Microphone (USB)", stop, sd=fake)
    assert frames and all(len(f) == FRAME and f.dtype == np.int16 for f in frames)


def test_voice_panel_in_the_browser(served):
    """One row per device, one lit at a time, and the speaker test's result on screen."""
    import os
    from pathlib import Path

    from test_display_browser import _launch, sync_api

    rt, url, calls, home = served
    rt._save_display_state(help_seen=True)
    rt.display.publish({"help_seen": True})
    sd = FakeSD()
    rt.settings._audio = lambda: audio.list_devices(sd)
    rt.settings.hooks["test_speaker"] = lambda d: True
    with sync_api.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url)
        page.locator("#setbtn").wait_for(state="visible", timeout=15000)
        page.click("#setbtn")
        page.click('#setnav button:text-is("Voice and hearing")')
        spk = page.locator('#setrows .row[data-id="speaker"]')
        spk.wait_for(timeout=30000)
        assert spk.locator("button.opt").count() == len(audio.list_devices(sd)["speakers"])
        assert spk.locator("button.opt.on").inner_text() == "System default"
        spk.locator("button", has_text="Headset Earphone (USB)").click()
        page.locator("#setmsg", has_text="Speaker set to Headset Earphone (USB)").wait_for(timeout=30000)
        assert page.locator('#setrows .row[data-id="speaker"] button.opt.on').count() == 1
        page.click('#setrows button:text-is("Test the speaker")')
        page.locator('#setrows .row[data-id="speaker_result"]', has_text="played on Headset").wait_for(timeout=30000)
        if os.environ.get("TAU_SCREENSHOTS"):
            Path(os.environ["TAU_SCREENSHOTS"]).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(os.environ["TAU_SCREENSHOTS"]) / "tau_r5_settings_voice.png"), full_page=True)
        browser.close()


if __name__ == "__main__":
    pytest.main([__file__])
