from __future__ import annotations

from openalphastack import doctor
from openalphastack.app import cli


def test_doctor_report_checks_plugin_mcp_skills_and_data_directory():
    report = doctor.build_report()
    names = {item["name"] for item in report["checks"]}

    assert report["ok"] is True
    assert {"plugin_manifest", "mcp_config", "domain_skills", "data_directory"} <= names


def test_doctor_cli_json(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda: {"schema_version": "v1", "ok": True, "checks": []})

    cli.main(["doctor", "--json"])

    assert '"ok": true' in capsys.readouterr().out


def test_doctor_treats_dashboard_dependency_as_optional(monkeypatch):
    real_import = doctor.importlib.import_module

    def fake_import(name: str):
        if name == "fastapi":
            raise ModuleNotFoundError(name)
        return real_import(name)

    monkeypatch.setattr(doctor.importlib, "import_module", fake_import)

    report = doctor.build_report()
    fastapi = next(item for item in report["checks"] if item["name"] == "import_fastapi")
    assert report["ok"] is True
    assert fastapi["ok"] is False
    assert fastapi["required"] is False
