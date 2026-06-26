#!/usr/bin/env bash
# scaffold.sh — create a new SwiftUI app from the skill templates and generate
# its Xcode project. Does NOT build/run (use the app's ./run.sh for that).
#
# Usage: scaffold.sh <AppName> [parent-dir] [bundle-prefix]
#   AppName        e.g. Notes  (non-alphanumerics are stripped for the scheme/bundle id)
#   parent-dir     where to create the app folder (default: current directory)
#   bundle-prefix  reverse-DNS prefix (default: com.example)
set -euo pipefail

RAW="${1:?Usage: scaffold.sh <AppName> [parent-dir] [bundle-prefix]}"
PARENT="${2:-$PWD}"
PREFIX="${3:-com.example}"

APP_NAME="$(printf '%s' "$RAW" | tr -cd '[:alnum:]')"
[ -n "$APP_NAME" ] || { echo "ERROR: app name must contain letters/digits"; exit 1; }

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TPL="$SKILL_DIR/templates"
DEST="$PARENT/$APP_NAME"
BUNDLE_ID="$PREFIX.$APP_NAME"

[ -e "$DEST" ] && { echo "ERROR: '$DEST' already exists; refusing to overwrite"; exit 1; }

mkdir -p "$DEST/Sources"
sed -e "s|__APP_NAME__|$APP_NAME|g" -e "s|__BUNDLE_PREFIX__|$PREFIX|g" -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" \
    "$TPL/project.yml" > "$DEST/project.yml"
sed -e "s|__APP_NAME__|$APP_NAME|g" "$TPL/App.swift" > "$DEST/Sources/${APP_NAME}App.swift"
cp "$TPL/ContentView.swift" "$DEST/Sources/ContentView.swift"
sed -e "s|__APP_NAME__|$APP_NAME|g" -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" "$TPL/run.sh" > "$DEST/run.sh"
chmod +x "$DEST/run.sh"

if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate --spec "$DEST/project.yml" --project "$DEST" >/dev/null
  echo "Generated $APP_NAME.xcodeproj"
else
  echo "WARN: xcodegen not installed; run 'brew install xcodegen' then 'xcodegen generate' in $DEST"
fi

echo "Scaffolded '$APP_NAME' at $DEST  (bundle id: $BUNDLE_ID)"
echo "Next:  cd \"$DEST\" && ./run.sh"
