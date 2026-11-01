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

## Releasing

Releases are made by the **Release** workflow (Actions → Release → *Run
workflow*, or `workflow_dispatch` through the API). Choose which part of the
version to increase (`patch`, `minor`, `major`), or enter an exact version
such as `1.2.0` or `1.2.0b1`, and whether it is a pre-release.

The workflow runs the full validation first, then:

1. works out the next version from the latest `v*` tag;
2. writes it into `manifest.json` inside the release zip (the repository
   itself keeps version `0.0.0.dev0`, so nobody edits version numbers by hand,
   and an install of the `main` branch shows up as a dev version);
3. zips `custom_components/wisemirror` as `wisemirror.zip`, which HACS
   installs (`zip_release` in `hacs.json`);
4. creates the tag and the GitHub release, with notes listing the commit
   subjects since the previous release.

Pre-releases (`X.Y.ZbN`) are offered by HACS to users who enable beta versions.
