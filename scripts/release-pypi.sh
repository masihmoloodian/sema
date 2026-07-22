#!/usr/bin/env bash
#
# Release a new version of the `sema-mcp` PyPI package.
#
#   scripts/release-pypi.sh <new-version>        e.g. scripts/release-pypi.sh 0.1.1
#
# Bumps the version in pyproject.toml, builds + validates the artifacts, then
# commits, tags `vX.Y.Z`, and pushes. The tag triggers
# .github/workflows/publish.yml, which publishes to PyPI via Trusted Publishing
# (no token). Requires the trusted publisher to be configured once on PyPI
# (pypi.org -> sema-mcp -> Settings -> Publishing).
#
# Prefer a direct local publish instead of the tagged CI flow? After this builds,
# you can run:  uv publish --token pypi-XXXX
#
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: scripts/release-pypi.sh <new-version>   (e.g. 0.1.1)" >&2
  exit 1
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([._-]?(a|b|rc|post|dev)[0-9]*)?$ ]]; then
  echo "error: '$VERSION' does not look like a version (e.g. 0.1.1)" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean; commit or stash first." >&2
  exit 1
fi

echo "==> pyproject.toml: version = \"$VERSION\""
python3 - "$VERSION" <<'PY'
import pathlib, re, sys
v = sys.argv[1]
p = pathlib.Path("pyproject.toml")
new, n = re.subn(r'(?m)^version = ".*"$', f'version = "{v}"', p.read_text(), count=1)
if n != 1:
    sys.exit("could not find the [project] version line in pyproject.toml")
p.write_text(new)
PY

echo "==> build + validate"
rm -rf dist
uv build
uvx twine check dist/*

echo "==> commit + tag v$VERSION"
git add pyproject.toml
git commit -m "release: v$VERSION"
git tag "v$VERSION"

echo "==> push (the tag triggers the PyPI publish workflow)"
git push origin main
git push origin "v$VERSION"

cat <<EOF

Released v$VERSION. Track it here:
  https://github.com/get-sema/sema/actions
  https://pypi.org/project/sema-mcp/
EOF
