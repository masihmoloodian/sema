#!/usr/bin/env bash
#
# Release a new version of the sema VS Code extension.
#
#   scripts/release-vscode.sh <patch|minor|major|x.y.z>            tag -> CI publishes
#   scripts/release-vscode.sh <patch|minor|major|x.y.z> --publish  publish directly (local PAT)
#
# Bumps vscode-extension/package.json, builds the .vsix, and commits the bump.
#   default:   pushes a `vscode-v<version>` tag that triggers
#              .github/workflows/publish-vscode.yml (needs the VSCE_PAT repo secret).
#   --publish: instead runs `vsce publish` locally (needs VSCE_PAT env, or a prior
#              `cd vscode-extension && npx vsce login MasihMoloodian`).
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
VER="${NEW#v}"
echo "    $NEW"

echo "==> package the .vsix (validates the build)"
npm run package
echo "    vscode-extension/sema-codebase-chat-$VER.vsix"

echo "==> commit version bump"
git add package.json package-lock.json
git commit -m "release(vscode): $NEW"
git push origin main

if [[ "$PUBLISH" == "--publish" ]]; then
  echo "==> vsce publish (direct)"
  npx vsce publish
  echo "Published $NEW to the VS Code Marketplace."
else
  echo "==> tag vscode-$NEW (triggers the publish workflow)"
  git tag "vscode-$NEW"
  git push origin "vscode-$NEW"
  cat <<EOF

Tagged vscode-$NEW. The Marketplace publish runs in CI (needs the VSCE_PAT secret):
  https://github.com/get-sema/sema/actions
Or upload vscode-extension/sema-codebase-chat-$VER.vsix manually at
  https://marketplace.visualstudio.com/manage/publishers/MasihMoloodian
EOF
fi
