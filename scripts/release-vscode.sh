#!/usr/bin/env bash
#
# Package (and optionally publish) a new version of the sema VS Code extension.
#
#   scripts/release-vscode.sh <patch|minor|major|x.y.z>            build the .vsix
#   scripts/release-vscode.sh <patch|minor|major|x.y.z> --publish  build + publish
#
# Bumps vscode-extension/package.json, builds the .vsix, and commits the bump.
# With --publish it also uploads to the Marketplace via `vsce publish`, which
# needs a Personal Access Token: set VSCE_PAT, or run
#   cd vscode-extension && npx vsce login MasihMoloodian
# once beforehand.
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vscode-extension"

BUMP="${1:-}"
PUBLISH="${2:-}"
if [[ -z "$BUMP" ]]; then
  echo "usage: scripts/release-vscode.sh <patch|minor|major|x.y.z> [--publish]" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean; commit or stash first." >&2
  exit 1
fi

echo "==> npm install"
npm install --silent

echo "==> bump version ($BUMP)"
NEW=$(npm version "$BUMP" --no-git-tag-version)   # prints vX.Y.Z
echo "    $NEW"

echo "==> package the .vsix"
npm run package
VSIX="sema-codebase-chat-${NEW#v}.vsix"

echo "==> commit version bump"
git add package.json package-lock.json
git commit -m "release(vscode): $NEW"
git push origin main

if [[ "$PUBLISH" == "--publish" ]]; then
  echo "==> vsce publish"
  npx vsce publish
  echo "Published $NEW to the VS Code Marketplace."
else
  cat <<EOF

Built vscode-extension/$VSIX (version ${NEW#v}). To publish it:
  - upload it at https://marketplace.visualstudio.com/manage/publishers/MasihMoloodian
  - or re-run with --publish (needs a PAT): scripts/release-vscode.sh $BUMP --publish
EOF
fi
