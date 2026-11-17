import shutil
from pathlib import Path

from tau import okf
from tau.knowledge.fts import FTSIndex
from tau.knowledge.search import search_okf

SAMPLE = Path(__file__).parent / "sample_okf"


def test_sample_bundle_validates_and_trust_fields_are_read():
    rep = okf.validate(SAMPLE)
    assert rep.ok, rep.errors
    concepts = {c.id: c for c in okf.iter_concepts(SAMPLE)}
    assert concepts["playbooks/backup"].tier == "human-reviewed"      # bare mapping = one-element list
    assert concepts["services/backup_service"].tier == "machine-confirmed"
    assert concepts["playbooks/backup_old"].tier == "unverified"
    assert concepts["services/backup_service"].stale                    # stale_after is in the past
    assert concepts["playbooks/backup_old"].status == "deprecated"
    assert concepts["playbooks/backup"].status == "stable"


def test_search_prefers_verified_and_warns_on_stale():
    hits = search_okf(SAMPLE, "backup")
    assert hits[0]["id"] == "playbooks/backup"                         # verified beats keyword-stuffed
    stale = next(h for h in hits if h["id"] == "services/backup_service")
    assert "stale_after" in stale["warning"]
    old = next(h for h in hits if h["id"] == "playbooks/backup_old")
    assert "deprecated" in old["warning"]


def test_validator_catches_errors(tmp_path):
    b = tmp_path / "b"
    shutil.copytree(SAMPLE, b)
    (b / "loose.md").write_text("no frontmatter here\n")
    (b / "typeless.md").write_text("---\ntitle: x\n---\nbody\n")
    (b / "broken.md").write_text("---\ntype: [unclosed\n---\n")
    (b / "services" / "index.md").write_text("---\nokf_version: '0.2'\n---\n# x\n")
    (b / "log.md").write_text("# Log\n\n## yesterday\n* thing\n")
    rep = okf.validate(b)
    joined = "\n".join(rep.errors)
    for needle in ("loose.md: no YAML frontmatter", "typeless.md: frontmatter has no 'type'",
                   "broken.md", "services/index.md", "log heading 'yesterday'"):
        assert needle in joined, needle


def test_write_produces_valid_okf_with_trust_fields(tmp_path):
    import datetime as dt
    root = tmp_path / "kb"
    okf.write_concept(root, "notes/gpu", "Reference", "# Notes\n\nThe card has 24 GB.",
                      title="GPU notes", description="What the GPU is.",
                      sources=[{"id": "smi", "resource": "nvidia-smi on the hub"}],
                      verified=[{"by": "human:owner", "at": okf.iso()}],
                      stale_after=okf.now_utc() + dt.timedelta(days=30), status="stable")
    rep = okf.validate(root)
    assert rep.ok and not rep.warnings, (rep.errors, rep.warnings)
    c = okf.read_concept(root / "notes" / "gpu.md", root)
    assert c.meta["generated"]["by"].startswith("tau/") and c.tier == "human-reviewed" and not c.stale
    assert "GPU notes" in (root / "notes" / "index.md").read_text()
    assert (root / "index.md").read_text().startswith("---\nokf_version")
    assert "## " in (root / "log.md").read_text()
    # round trip keeps unknown keys
    okf.write_concept(root, "x", "Thing", "b", extra={"custom_key": 5})
    assert okf.read_concept(root / "x.md", root).meta["custom_key"] == 5


def test_fts_index_queries_on_disk_and_skips_never_walk(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("The quantum flux capacitor needs calibration every week.\n" * 3)
    (docs / "b.txt").write_text("Groceries: apples, bread.\n")
    idx = FTSIndex(tmp_path / "fts.sqlite")
    assert idx.index_folder(docs)["indexed"] == 2
    assert idx.index_folder(docs)["unchanged"] == 2
    hits = idx.search("capacitor calibration")
    assert hits and hits[0]["path"].endswith("a.md") and "[" in hits[0]["snippet"]
    import pytest
    with pytest.raises(PermissionError):
        idx.index_folder(docs, never_walk=[str(tmp_path)])
