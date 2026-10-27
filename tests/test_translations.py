"""Checks on the translation files and the metadata."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "wisemirror"


def _flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out.update(_flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _load(path: Path) -> dict[str, str]:
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


def test_english_matches_strings() -> None:
    """translations/en.json is a copy of strings.json."""
    assert _load(INTEGRATION / "translations" / "en.json") == _load(
        INTEGRATION / "strings.json"
    )


@pytest.mark.parametrize("language", ["de", "es"])
def test_translation_has_every_string_and_placeholder(language: str) -> None:
    english = _load(INTEGRATION / "strings.json")
    translated = _load(INTEGRATION / "translations" / f"{language}.json")
    assert set(translated) == set(english)
    for key, text in english.items():
        assert set(re.findall(r"\{\w+\}", translated[key])) == set(
            re.findall(r"\{\w+\}", text)
        ), key


def test_every_raised_error_has_a_message() -> None:
    raised = {
        match
        for path in INTEGRATION.glob("*.py")
        for match in re.findall(
            r'translation_domain=DOMAIN,\s*translation_key="(\w+)"',
            path.read_text(encoding="utf-8"),
        )
    }
    messages = {
        key.split(".")[1]
        for key in _load(INTEGRATION / "strings.json")
        if key.startswith("exceptions.")
    }
    assert raised
    assert raised == messages


def test_every_entity_translation_key_exists() -> None:
    strings = _load(INTEGRATION / "strings.json")
    for path in INTEGRATION.glob("*.py"):
        platform = path.stem
        for key in re.findall(
            r'translation_key\s*=\s*"(\w+)"', path.read_text(encoding="utf-8")
        ):
            if f"entity.{platform}.{key}.name" not in strings:
                assert path.stem in {"coordinator", "services"} or any(
                    k.startswith(f"entity.{platform}.{key}.") for k in strings
                ), f"{platform}.{key}"


def test_icons_cover_entities() -> None:
    icons = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    for platform, entities in strings["entity"].items():
        for key in entities:
            assert key in icons["entity"].get(platform, {}), f"{platform}.{key}"


def test_manifest_version_matches_pyproject() -> None:
    import tomllib

    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads(
        (INTEGRATION.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["version"] == project["project"]["version"]
