import os
import subprocess
import sys
from pathlib import Path

from conftest import make_profile

ROOT = Path(__file__).resolve().parents[1]


def tau(*args, home):
    env = dict(os.environ, TAU_HOME=str(home), PYTHONPATH=str(ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "tau", *args], capture_output=True, text=True, env=env,
                          timeout=120)


def test_cli_basics(tau_home):
    make_profile(tau_home)
    r = tau("okf", "validate", str(ROOT / "tests" / "sample_okf"), home=tau_home)
    assert r.returncode == 0 and "valid OKF" in r.stdout
    r = tau("grant", "gpu", "sense", "--yes", home=tau_home)
    assert "gpu is now at sense" in r.stdout, r.stderr
    r = tau("status", home=tau_home)
    assert "gpu=sense" in r.stdout and "voice=act" in r.stdout
    r = tau("hook", "bash", home=tau_home)
    assert "PROMPT_COMMAND" in r.stdout
    r = tau("wrap", "--", sys.executable, "-c", "import sys; sys.exit(4)", home=tau_home)
    assert r.returncode == 4
    r = tau("doctor", home=tau_home)
    assert "[  ok ] profile" in r.stdout and "brain" in r.stdout


def test_runtime_builds_without_audio_and_handles_display_clicks(tau_home):
    from tau.runtime import Runtime

    data = make_profile(tau_home)
    rt = Runtime(data).build(want_voice=False)
    port = rt.display.serve(0)
    try:
        assert rt.on_action({"core": True})["ok"] is False   # no mic here: says so plainly
        rt.assistant.pins.pin("hello", "world")
        res = rt.on_action({"segment": "pins"})
        assert res["pins"][0]["text"] == "world" and rt.assistant.pins.waiting() == []
        assert rt.on_action({"segment": "mic"})["ok"] and rt.assistant.muted
        assert "gpu organ is off" in rt.on_action({"segment": "gpu"})["say"]
        assert rt.on_text("quiet")["ok"]
    finally:
        rt.display.stop()
