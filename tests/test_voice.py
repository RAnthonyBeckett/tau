import threading
import time

import numpy as np

from tau.voice import TTSEngine, Voice


class ToneEngine(TTSEngine):
    def synth(self, text, voice):
        rate = 24000
        t = np.arange(rate * 2) / rate  # two seconds
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), rate


class SlowSink:
    def __init__(self):
        self.blocks = 0
        self.started = threading.Event()

    def open(self, rate):
        pass

    def write(self, block):
        self.blocks += 1
        self.started.set()
        time.sleep(len(block) / 24000)


def test_speech_plays_levels_and_can_be_interrupted():
    sink, levels, states = SlowSink(), [], []
    v = Voice(ToneEngine(), sink, on_level=levels.append, on_state=states.append)
    v.say("hello")
    assert sink.started.wait(2)
    time.sleep(0.2)
    v.interrupt()           # a click on the core, or hearing the name
    assert not v.is_speaking() and v.interrupted
    assert 0 < sink.blocks < 50  # two seconds would be 50 blocks
    assert max(levels) > 0.3 and levels[-1] == 0.0
    assert "speaking" in states and states[-1] == "idle"
