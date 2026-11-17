"""Model engines: never mandatory, Ollama offered only when nothing is found,
and installing it never waits on its setup program's process tree (bug 4)."""
import io
import json

from tau import engines


class NeverWaits:
    """Stands in for OllamaSetup.exe: its tray app keeps the process tree alive for ever."""

    def __init__(self, argv, **kw):
        self.argv = argv

    def wait(self, *a, **k):
        raise AssertionError("waited on the setup program's process tree")


def test_windows_install_polls_instead_of_waiting():
    started, said, polls = [], [], {"n": 0}

    def popen(argv, **kw):
        started.append(argv)
        return NeverWaits(argv)

    def running():
        polls["n"] += 1
        return polls["n"] > 6          # the server comes up after a few polls

    ok = engines.install_ollama(said.append, popen=popen, running=running, find=lambda: None,
                                download=lambda url, dest, say, label: None, sleep=lambda s: None,
                                system="Windows", poll=10)
    assert ok
    assert started[0][1:] == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
    assert any("1.1 GB" in s for s in said)            # size said before the download
    assert any("Still installing" in s for s in said)   # never silent while polling


def test_install_gives_up_plainly_after_the_time_limit():
    said = []
    ok = engines.install_ollama(said.append, popen=lambda a, **k: NeverWaits(a), running=lambda: False,
                                find=lambda: None, download=lambda *a: None, sleep=lambda s: None,
                                system="Windows", timeout=30, poll=5)
    assert not ok and "check again" in said[-1]


def test_installed_but_not_running_starts_the_server_once():
    started = []
    state = {"n": 0}

    def running():
        state["n"] += 1
        return state["n"] > 12

    engines.install_ollama(lambda s: None, popen=lambda a, **k: started.append(a) or NeverWaits(a),
                           running=running, find=lambda: "/opt/ollama", sleep=lambda s: None,
                           system="Linux", poll=5)
    assert started == [["/opt/ollama", "serve"]]


def test_pull_model_reports_progress_and_success():
    events = [{"status": "pulling", "total": 1000, "completed": 500},
              {"status": "pulling", "total": 1000, "completed": 1000}, {"status": "success"}]
    body = io.BytesIO("\n".join(json.dumps(e) for e in events).encode())
    said = []
    assert engines.pull_model("qwen3:1.7b", said.append, every=0, opener=lambda req, timeout: _Ctx(body))
    assert any("of" in s and "MB" in s for s in said) and said[-1].endswith("is ready.")


class _Ctx:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self.f

    def __exit__(self, *a):
        return False


def test_starter_model_is_sized_to_the_machine():
    assert engines.pick_starter(24, 64).tag == "qwen3:8b"
    assert engines.pick_starter(6, 16).tag == "qwen3:4b"
    assert engines.pick_starter(None, 8).tag == "qwen3:1.7b"


def test_rank_models_by_fit_not_alphabet():
    models = ["alpha-70b", "beta-1.5b", "gamma-8b-instruct", "delta-14b"]
    ranked, why = engines.rank_models(models, vram_gb=8, ram_gb=32)
    names = [r.name for r in ranked]
    assert names[0] == "gamma-8b-instruct"           # biggest that fits an 8 GB card
    assert names.index("beta-1.5b") < names.index("delta-14b") < names.index("alpha-70b")
    assert why and "8 GB" in why


def test_no_reason_no_recommendation():
    ranked, why = engines.rank_models(["mystery", "other"], vram_gb=12, ram_gb=32)
    assert why is None and [r.name for r in ranked] == ["mystery", "other"]
