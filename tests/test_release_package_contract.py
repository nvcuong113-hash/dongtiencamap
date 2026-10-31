from pathlib import Path
import importlib


def test_streamlit_entrypoint_and_shadow_package_are_at_repository_root():
    root = Path(__file__).resolve().parents[1]
    assert (root / "app.py").is_file()
    assert (root / "quants" / "__init__.py").is_file()
    assert (root / "quants" / "smf_v2" / "__init__.py").is_file()
    assert (root / "quants" / "smf_v2" / "persistence.py").is_file()
    assert (root / "quants" / "smf_v2" / "fusion.py").is_file()
    assert (root / "quants" / "forecast_validation.py").is_file()


def test_runtime_critical_modules_import_without_streamlit_app_side_effects():
    for module in (
        "quants.db", "quants.engine", "quants.providers", "quants.vsa_chart",
        "quants.smf_v2", "quants.smf_v2.persistence", "quants.smf_v2.fusion",
        "quants.forecast_validation",
    ):
        assert importlib.import_module(module) is not None
