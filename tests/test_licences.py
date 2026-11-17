import importlib.metadata as md
from email.message import Message

import pytest

from tau import licences
from tau.licences import check, classify, markdown_table, non_commercial, write_readme


class FakeDist:
    def __init__(self, name, licence="", requires=(), classifiers=(), expr=""):
        self.version = "1.0"
        self.requires = list(requires)
        m = Message()
        m["Name"] = name
        if licence:
            m["License"] = licence
        if expr:
            m["License-Expression"] = expr
        for c in classifiers:
            m["Classifier"] = c
        self.metadata = m


def fake_env(dists):
    by = {d.metadata["Name"].lower(): d for d in dists}

    def get(name):
        try:
            return by[name.lower().replace("_", "-")]
        except KeyError:
            raise md.PackageNotFoundError(name)
    return get


def test_classify():
    assert classify("MIT") == "permissive"
    assert classify("Apache-2.0 OR BSD-3-Clause") == "permissive"
    assert classify("GPL-3.0-or-later") == "copyleft"
    assert classify("GNU Affero General Public License v3") == "copyleft"
    assert classify("LGPL-2.1-or-later") == "weak-copyleft"
    assert classify("MPL-2.0 AND MIT") == "weak-copyleft"
    assert classify("GPL-3.0 WITH GCC Runtime Library exception") == "weak-copyleft"
    assert classify("LGPL-2.1 AND GPL-3.0") == "copyleft"
    assert classify("LicenseRef-NVIDIA-Proprietary") == "proprietary"
    assert classify("") == "unknown"
    assert non_commercial("cc-by-nc-4.0") and non_commercial("CC BY-NC-SA 4.0") and not non_commercial("apache-2.0")


def test_gate_passes_on_the_current_tree():
    rep = check(live_models=False)
    assert rep.ok, rep.problems
    assert any(m["repo"] == "hexgrad/Kokoro-82M" for m in rep.models)


def test_gate_fails_when_a_gpl_package_is_injected(monkeypatch):
    monkeypatch.setattr(licences, "no_deps_packages", lambda: [])
    good = [FakeDist("tau", requires=["goodpkg", "lgplpkg; extra == 'voice'"]),
            FakeDist("goodpkg", classifiers=["License :: OSI Approved :: MIT License"]),
            FakeDist("lgplpkg", expr="LGPL-2.1-only")]
    rep = check(fake_env(good), live_models=False)
    assert rep.ok and rep.packages["lgplpkg"].verdict == "weak-copyleft"
    bad = good + [FakeDist("phonemizer-fork", licence="GPL-3.0")]
    bad[0] = FakeDist("tau", requires=["goodpkg", "lgplpkg; extra == 'voice'",
                                        "phonemizer-fork; extra == 'voice'"])
    rep = check(fake_env(bad), live_models=False)
    assert not rep.ok and any("phonemizer-fork" in p and "voice" in p for p in rep.problems)
    # a GPL package deep in the tree is found too
    deep = [FakeDist("tau", requires=["goodpkg"]),
            FakeDist("goodpkg", expr="MIT", requires=["midpkg"]),
            FakeDist("midpkg", expr="BSD-3-Clause", requires=["agplpkg"]),
            FakeDist("agplpkg", expr="AGPL-3.0-only")]
    rep = check(fake_env(deep), live_models=False)
    assert any("agplpkg" in p for p in rep.problems)


def test_gate_fails_on_a_non_commercial_model(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text('path = hf_hub_download(repo_id="someone/wake-model", filename="x.onnx")\n')
    monkeypatch.setitem(licences.MODELS, "someone/wake-model", {"licence": "cc-by-nc-sa-4.0", "used_by": "t"})
    monkeypatch.setattr(licences, "no_deps_packages", lambda: [])
    rep = check(fake_env([FakeDist("tau")]), live_models=False, src=src)
    assert any("non-commercial" in p for p in rep.problems)
    (src / "m.py").write_text('m = BartForConditionalGeneration.from_pretrained("unknown/model")\n')
    rep = check(fake_env([FakeDist("tau")]), live_models=False, src=src)
    assert any("unknown/model" in p and "no known licence" in p for p in rep.problems)


def test_readme_table_is_generated_from_the_same_data(tmp_path, monkeypatch):
    monkeypatch.setattr(licences, "no_deps_packages", lambda: [])
    rep = check(fake_env([FakeDist("tau", requires=["goodpkg"]), FakeDist("goodpkg", expr="MIT")]),
                live_models=False)
    readme = tmp_path / "README.md"
    readme.write_text("# x\n\nintro\n")
    assert write_readme(rep, readme)
    t = readme.read_text()
    assert licences.TABLE_START in t and "| goodpkg | 1.0 | MIT | core |" in t
    assert not write_readme(rep, readme)          # stable: a second run changes nothing
    # the shipped README carries a generated table with no GPL rows
    from tau import paths
    real = paths.shipped_file("README.md").read_text()
    assert licences.TABLE_START in real and licences.TABLE_END in real
    block = real[real.index(licences.TABLE_START):real.index(licences.TABLE_END)]
    for row in block.splitlines():
        if row.startswith("| ") and not row.startswith(("| Package", "| Model", "|---")):
            assert classify(row.split("|")[3]) != "copyleft", row
