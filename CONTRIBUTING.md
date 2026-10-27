# Contributing

Reports from real mirrors are the most useful contribution: only part of the
protocol has been confirmed on hardware. Use the **Device report** issue template, and include the output of:

```bash
python3 scripts/wisemirror.py discover
python3 scripts/wisemirror.py status <mirror-ip>
```

The script needs only Python 3.12+, not Home Assistant.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff format custom_components tests scripts
ruff check custom_components tests scripts
```

The tests run the UDP, TCP and HTTP paths against a simulated mirror on
localhost (`tests/fake_mirror.py`). `requirements_test_min.txt` pins the oldest
supported Home Assistant; CI runs the suite against both.

Translations live in `custom_components/wisemirror/translations/`. `strings.json`
and `translations/en.json` must stay identical, and every other language must
have the same keys and placeholders; `tests/test_translations.py` checks this.

To release, bump `version` in `manifest.json` and `pyproject.toml` and publish
a GitHub release tagged `v<version>`.
