from __future__ import annotations

from openalphastack.paths import PROJECT_ROOT


SKILLS = ("market-analyzer", "stock-screener", "stock-analyzer", "t0-intraday")


def test_domain_skills_require_versioned_mcp_envelopes_and_freshness():
    for skill in SKILLS:
        text = (PROJECT_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "schema_version" in text, skill
        assert "`ok`" in text or "`ok=false`" in text, skill
        assert "meta.source" in text, skill
        assert "meta.as_of" in text, skill
        assert "meta.freshness.status" in text, skill
        assert "Demo" in text, skill


def test_research_skills_route_offline_work_to_read_only_demo_dataset():
    for skill in SKILLS[:3]:
        text = (PROJECT_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "read_demo_dataset" in text or "Demo 数据集" in text, skill
