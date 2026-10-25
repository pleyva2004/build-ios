#!/usr/bin/env bash
# scaffold.sh — create a new live-UI SwiftUI prototype and generate its Xcode project.
# Does NOT build/run (use the app's ./run.sh once; after that, UI changes hot-reload).
#
# Usage: scaffold.sh <AppName> [parent-dir] [bundle-prefix]
#   AppName        e.g. Notes  (non-alphanumerics are stripped for the scheme/bundle id)
#   parent-dir     where to create the app folder (default: current directory)
#   bundle-prefix  reverse-DNS prefix (default: com.example)
set -euo pipefail

RAW="${1:?Usage: scaffold.sh <AppName> [parent-dir] [bundle-prefix]}"
PARENT="${2:-$PWD}"
PREFIX="${3:-com.example}"

APP_NAME="$(printf '%s' "$RAW" | LC_ALL=C tr -cd 'A-Za-z0-9')"   # ASCII only: bundle ids allow A-Z a-z 0-9 . -
[ -n "$APP_NAME" ] || { echo "ERROR: app name must contain letters/digits"; exit 1; }
case "$APP_NAME" in [0-9]*) APP_NAME="App$APP_NAME" ;; esac   # Swift type names can't start with a digit
case "$PREFIX" in ''|*[!A-Za-z0-9.-]*) echo "ERROR: bundle prefix may only contain letters, digits, '.' and '-'"; exit 1 ;; esac

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TPL="$SKILL_DIR/templates"
DEST="$PARENT/$APP_NAME"
BUNDLE_ID="$PREFIX.$APP_NAME"

[ -e "$DEST" ] && { echo "ERROR: '$DEST' already exists; refusing to overwrite"; exit 1; }

subst() { sed -e "s|__APP_NAME__|$APP_NAME|g" -e "s|__BUNDLE_PREFIX__|$PREFIX|g" -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" "$1"; }

mkdir -p "$DEST/Sources/Runtime" "$DEST/Sources/Components" "$DEST/tools" "$DEST/.claude/skills/ui"

# Xcode project spec + entry point + the live runtime (Swift, never edited for UI work)
subst "$TPL/project.yml" > "$DEST/project.yml"
subst "$TPL/App.swift" > "$DEST/Sources/${APP_NAME}App.swift"
cp "$TPL"/Runtime/*.swift "$DEST/Sources/Runtime/"
cp "$TPL"/Components/*.swift "$DEST/Sources/Components/"

# The UI itself (JSON) + the engine CLI and its reference
cp -R "$TPL/design" "$DEST/design"
subst "$TPL/design/app.json" > "$DEST/design/app.json"
cp "$TPL/ui" "$DEST/ui"
cp "$TPL/tools/REFERENCE.md" "$TPL/tools/evals.jsonl" "$TPL/tools/eject.py" "$DEST/tools/"
subst "$TPL/run.sh" > "$DEST/run.sh"
chmod +x "$DEST/run.sh" "$DEST/ui"

# Claude Code: instructions, the /ui fast-path skill, hooks + permissions
subst "$TPL/CLAUDE.md" > "$DEST/CLAUDE.md"
cp "$TPL/claude/skills/ui/SKILL.md" "$DEST/.claude/skills/ui/SKILL.md"
cp "$TPL/claude/settings.json" "$DEST/.claude/settings.json"
cp "$TPL/gitignore" "$DEST/.gitignore"

if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate --spec "$DEST/project.yml" --project "$DEST" >/dev/null
  echo "Generated $APP_NAME.xcodeproj"
else
  echo "WARN: xcodegen not installed; run 'brew install xcodegen' then 'xcodegen generate' in $DEST"
fi

echo "Scaffolded '$APP_NAME' at $DEST  (bundle id: $BUNDLE_ID)"
echo "Next:  cd \"$DEST\" && ./run.sh        # first build + launch (once)"
echo "Then:  ./ui ask \"make the header warmer\"   or   claude --model haiku  →  /ui <change>"
