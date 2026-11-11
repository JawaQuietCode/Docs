from pathlib import Path
from skills import loader


def test_aifs_tutor_skills_are_discovered_natively():
    loader.clear_cache()
    names = {x["name"] for x in loader._scan(loader.EXTERNAL_DIR, "external")}
    assert {"start-learning", "learn", "course-guide", "learn-reverse-engineering"} <= names


def test_aifs_upstream_license_and_attribution_are_bundled():
    root = Path(__file__).resolve().parents[1] / "skills/external/ai-engineering-from-scratch"
    assert (root / "LICENSE").is_file()
    assert "rohitg00/ai-engineering-from-scratch" in (root / "UPSTREAM.md").read_text()


def test_fcc_cli_bootstrap_is_pinned_and_does_not_claim_shared_provider():
    root = Path(__file__).resolve().parents[1]
    startup = (root / "start.sh").read_text()
    assert "uv tool install --python 3.14.10" in startup
    assert "da29b69db4cf4854c6d6fb4195e5628db04c219d" in startup
    assert "not" in (root / "docs/RELEASE-V0.4.0.md").read_text().lower()
