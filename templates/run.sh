#!/usr/bin/env bash
# Build + run this app on the iOS Simulator, entirely from the command line.
# Usage:  ./run.sh                 # auto-picks a simulator (prefers iPhone 17 Pro)
#         ./run.sh "iPhone Air"    # any installed simulator, by name
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEME="__APP_NAME__"
BUNDLE_ID="__BUNDLE_ID__"
DERIVED="$PROJECT_DIR/build"
APP="$DERIVED/Build/Products/Debug-iphonesimulator/$SCHEME.app"
SHOT="$PROJECT_DIR/$SCHEME-screenshot.png"

# Choose a simulator: arg 1, else iPhone 17 Pro, else first available iPhone.
DEVICE="${1:-iPhone 17 Pro}"
if ! xcrun simctl list devices available | grep -q "$DEVICE ("; then
  alt="$(xcrun simctl list devices available | grep -oE 'iPhone [^(]*' | head -1 | sed 's/ *$//')"
  [ -n "$alt" ] && DEVICE="$alt"
fi

echo ">> Regenerating project from project.yml (XcodeGen)"
xcodegen generate --spec "$PROJECT_DIR/project.yml" --project "$PROJECT_DIR" >/dev/null

build_once() {
  xcodebuild \
    -project "$PROJECT_DIR/$SCHEME.xcodeproj" \
    -scheme "$SCHEME" \
    -configuration Debug \
    -destination 'generic/platform=iOS Simulator' \
    -derivedDataPath "$DERIVED" \
    build 2>&1 | xcbeautify
}

echo ">> Building $SCHEME (Debug, iOS Simulator)"
if ! build_once; then
  # The first build after a fresh Xcode install can fail with a transient
  # swift-plugin-server / macro "malformed response" error. Retrying clears it.
  echo ">> First build failed (likely a transient macro-plugin hiccup) — retrying once..."
  build_once
fi

echo ">> Booting simulator: $DEVICE"
xcrun simctl boot "$DEVICE" 2>/dev/null || true
xcrun simctl bootstatus "$DEVICE" -b
open -a Simulator

echo ">> Installing and launching $BUNDLE_ID"
xcrun simctl install "$DEVICE" "$APP"
xcrun simctl launch "$DEVICE" "$BUNDLE_ID"

echo ">> Capturing screenshot to: $SHOT"
sleep 3
xcrun simctl io "$DEVICE" screenshot "$SHOT"
echo ">> Done. $SCHEME is running on $DEVICE."
