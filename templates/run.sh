#!/usr/bin/env bash
# Build + launch this app on the iOS Simulator.
#
# You rarely need this. After the first launch the app hot-reloads design/*.json,
# so UI changes (./ui apply, ./ui ask, /ui in Claude Code) show up in ~0.2 s with
# no rebuild. Re-run only after changing Swift (Sources/Components) or project.yml.
#
# Usage:  ./run.sh                    build (incremental) + launch + screenshot
#         ./run.sh "iPhone Air"       pick a simulator by name
#         ./run.sh --launch           relaunch without building
#         ./run.sh --no-shot          skip the screenshot
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEME="__APP_NAME__"
BUNDLE_ID="__BUNDLE_ID__"
DERIVED="$PROJECT_DIR/build"
APP="$DERIVED/Build/Products/Debug-iphonesimulator/$SCHEME.app"
LIVE="$PROJECT_DIR/.live"
mkdir -p "$LIVE"
START=$(date +%s)

BUILD=1
SHOT=1
DEVICE_ARG=""
for arg in "$@"; do
  case "$arg" in
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    --launch|--no-build) BUILD=0 ;;
    --no-shot) SHOT=0 ;;
    *) DEVICE_ARG="$arg" ;;
  esac
done

case "$PROJECT_DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    echo "!! This project is in a macOS-protected folder. The Simulator may be unable to read"
    echo "   design/, which turns hot reload off. Move it to e.g. ~/Code." ;;
esac

# --- 1. Simulator (iOS 26+ only — the deployment target):
#        named > last used > already booted > iPhone 17 Pro > newest iPhone
DEVICES_JSON="$(xcrun simctl list devices available -j)"
pick() {  # pick <name or ""> <booted-only 0|1> [udid]  -> UDID of the newest matching iOS 26+ iPhone
  printf '%s' "$DEVICES_JSON" | python3 -c '
import json, re, sys
name, booted, want = sys.argv[1], sys.argv[2] == "1", (sys.argv[3] if len(sys.argv) > 3 else "")
best = None
for runtime, devices in json.load(sys.stdin)["devices"].items():
    m = re.search(r"iOS-(\d+)-(\d+)", runtime)
    if not m or int(m.group(1)) < 26:
        continue
    version = (int(m.group(1)), int(m.group(2)))
    for d in devices:
        if want and d["udid"] != want:
            continue
        if not want and not d["name"].startswith("iPhone"):
            continue
        if name and d["name"] != name:
            continue
        if booted and d["state"] != "Booted":
            continue
        if best is None or version > best[0]:
            best = (version, d["udid"])
print(best[1] if best else "")' "$1" "$2" "${3:-}"
}
UDID=""
if [ -n "$DEVICE_ARG" ]; then
  UDID="$(pick "$DEVICE_ARG" 0)"
  [ -n "$UDID" ] || { echo "No available iOS 26+ simulator named '$DEVICE_ARG' (see: xcrun simctl list devices available)"; exit 1; }
fi
[ -z "$UDID" ] && [ -f "$LIVE/device" ] && UDID="$(pick "" 0 "$(cat "$LIVE/device")")"
[ -z "$UDID" ] && UDID="$(pick "" 1)"
[ -z "$UDID" ] && UDID="$(pick "iPhone 17 Pro" 0)"
[ -z "$UDID" ] && UDID="$(pick "" 0)"
[ -n "$UDID" ] || { echo "No iOS 26+ iPhone simulator found — run scripts/doctor.sh from the build-ios skill."; exit 1; }
echo "$UDID" > "$LIVE/device"

# --- 2. Boot in the background so it overlaps the build
if ! xcrun simctl list devices booted | grep -q "$UDID"; then
  echo ">> Booting simulator $UDID"
  (xcrun simctl boot "$UDID" >/dev/null 2>&1 || true) &
fi
pgrep -xq Simulator || open -a Simulator --args -CurrentDeviceUDID "$UDID" >/dev/null 2>&1 || true

# --- 3. Build (incremental; one CPU arch; no index store)
if [ "$BUILD" = 1 ]; then
  if [ -d "$PROJECT_DIR/$SCHEME.xcodeproj" ]; then
    xcodegen generate --spec "$PROJECT_DIR/project.yml" --project "$PROJECT_DIR" --use-cache >/dev/null
  else
    xcodegen generate --spec "$PROJECT_DIR/project.yml" --project "$PROJECT_DIR" >/dev/null
  fi
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then ARCH=arm64; else ARCH=x86_64; fi
  build_once() {
    xcodebuild \
      -project "$PROJECT_DIR/$SCHEME.xcodeproj" \
      -scheme "$SCHEME" \
      -configuration Debug \
      -destination 'generic/platform=iOS Simulator' \
      -derivedDataPath "$DERIVED" \
      ARCHS="$ARCH" ONLY_ACTIVE_ARCH=YES COMPILER_INDEX_STORE_ENABLE=NO \
      build 2>&1 | { if command -v xcbeautify >/dev/null 2>&1; then xcbeautify --quiet; else grep -E "error:|warning: |BUILD (SUCCEEDED|FAILED)" || true; fi; }
  }
  echo ">> Building $SCHEME (Debug, $ARCH)"
  if ! build_once; then
    # The first build after a fresh Xcode install can hit a transient
    # swift-plugin-server error; one retry clears it.
    echo ">> Build failed — retrying once"
    build_once
  fi
fi
[ -d "$APP" ] || { echo "No built app at $APP — run ./run.sh without --launch first."; exit 1; }

# --- 4. Install + relaunch, pointing the app at this folder for hot reload
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl install "$UDID" "$APP"
LAUNCHED=$(date +%s)
export SIMCTL_CHILD_UI_PROJECT_DIR="$PROJECT_DIR"
if ! xcrun simctl launch --terminate-running-process "$UDID" "$BUNDLE_ID" -UIProjectDir "$PROJECT_DIR" >/dev/null 2>&1; then
  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
  xcrun simctl launch "$UDID" "$BUNDLE_ID" -UIProjectDir "$PROJECT_DIR" >/dev/null
fi

# --- 5. Wait for the app to report a live link (no fixed sleep)
if python3 - "$LIVE/status.json" "$LAUNCHED" <<'PY'
import json, sys, time
path, since = sys.argv[1], float(sys.argv[2]) - 1
for _ in range(80):
    try:
        if json.load(open(path)).get("launchedAt", 0) >= since:
            sys.exit(0)
    except Exception:
        pass
    time.sleep(0.1)
sys.exit(1)
PY
then
  echo ">> Live: edits to design/*.json now hot-reload (try: ./ui ask \"make the title bigger\")"
else
  echo "!! The app did not report a live link, so hot reload is off. Check: ./ui status"
fi

if [ "$SHOT" = 1 ]; then
  sleep 0.5
  xcrun simctl io "$UDID" screenshot "$LIVE/shot.png" >/dev/null 2>&1 && echo ">> Screenshot: $LIVE/shot.png"
fi
echo ">> Done in $(( $(date +%s) - START ))s — $SCHEME is running on $UDID"
