# Publishing claudius to PyPI

claudius is a pip-installable package (distribution `claudius`, CLI `mas`, import
root `core`). The wheel bundles the framework files as package data and
`mas init-workspace` copies them into a `$MAS_HOME` workspace. Everything up to
**publishing** is automated/verified; the final upload needs *your* PyPI account.

## 0. One-time: confirm the name is free

`claudius` must be available on PyPI (it is unrelated to the `mas` distribution
name we avoided). Check:

```bash
pip index versions claudius   # or visit https://pypi.org/project/claudius/
```

If taken, change `name = "claudius"` in `pyproject.toml` to an available name
(the CLI command `mas` and import root `core` do not need to change).

## 1. Set the version

`pyproject.toml` currently declares `version = "0.2.0"`. The public git tag is
`v0.1.0` — reconcile before publishing so the tag, the wheel, and PyPI agree.
Pick the version you want to ship, update `pyproject.toml`, and tag it (step 6).

## 2. Build

```bash
rm -rf dist
UV_LINK_MODE=copy uv build          # produces dist/claudius-<ver>-py3-none-any.whl + .tar.gz
uvx twine check dist/*              # metadata sanity
```

Verify the wheel bundles assets and excludes tests:

```bash
python - <<'PY'
import zipfile, glob
z = zipfile.ZipFile(glob.glob("dist/*.whl")[0]); n = z.namelist()
assert any(x.startswith("core/_bundled/agents/") for x in n)
assert any(x.startswith("core/_bundled/skills/") for x in n)
assert "core/_bundled/mas/system_config.yaml" in n
assert not any("tests/" in x for x in n)
print("wheel OK:", len(n), "members")
PY
```

## 3. Smoke-test the wheel in a clean venv (no clone)

```bash
python -m venv /tmp/claudius-test && /tmp/claudius-test/bin/pip install dist/*.whl
export MAS_HOME=/tmp/claudius-ws
/tmp/claudius-test/bin/mas init-workspace      # copies from bundled package data
/tmp/claudius-test/bin/mas doctor              # expect: runtime_mode = installed wheel + workspace, fail=0
/tmp/claudius-test/bin/mas init demo && /tmp/claudius-test/bin/mas status proj-*-demo
```

## 4. (Optional) TestPyPI dry run

```bash
uvx twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ claudius
```

## 5. Publish to PyPI

Two options:

- **Manual:** `uvx twine upload dist/*` (needs a PyPI API token in `~/.pypirc` or
  `TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-...`).
- **Trusted Publishing (recommended):** add a GitHub Actions release workflow using
  PyPI's OIDC trusted publisher (`pypa/gh-action-pypi-publish`) so no token is
  stored. Configure the trusted publisher at https://pypi.org/manage/account/publishing/.

## 6. Tag the release

```bash
git tag v<ver> && git push origin v<ver>
```

After publishing, users can `pip install claudius` then `mas init-workspace`.
Until then, install from the repo: `pip install git+https://github.com/RicardoSantos0/claudius.git`.
