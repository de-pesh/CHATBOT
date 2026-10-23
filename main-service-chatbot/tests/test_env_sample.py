"""The repo-root .env.sample is generated from Settings (scripts/gen_env_sample.py); this keeps it honest.

If either test fails after you added or changed a setting, run: python scripts/gen_env_sample.py
"""
import importlib.util
import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / ".env.sample"


def _preset_names() -> set[str]:
    spec = importlib.util.spec_from_file_location("gen_env_sample", ROOT / "scripts" / "gen_env_sample.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.PRESET)


def _dump(**kwargs) -> dict:
    settings = Settings(**kwargs)
    return {k: (v.get_secret_value() if hasattr(v, "get_secret_value") else v) for k, v in settings.model_dump().items()}


def test_every_setting_is_listed_in_the_sample():
    text = SAMPLE.read_text(encoding="utf-8")
    missing = [name for name in Settings.model_fields if not re.search(rf"^(# )?{name}=", text, re.M)]
    assert not missing, f"not in .env.sample: {missing} - run python scripts/gen_env_sample.py"


def test_the_sample_only_differs_from_the_code_defaults_in_its_presets(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    required = {name: "x" for name, field in Settings.model_fields.items() if field.is_required()}
    defaults = _dump(_env_file=None, **required)
    from_sample = _dump(_env_file=str(SAMPLE), _env_file_encoding="utf-8")
    differing = {name for name in defaults if defaults[name] != from_sample[name]}
    assert differing <= _preset_names(), (
        f"active lines in .env.sample that are not the code default: {sorted(differing - _preset_names())} "
        "- run python scripts/gen_env_sample.py"
    )
