#!/usr/bin/env bash
# doctor.sh — check whether this Mac is ready to build iOS apps.
# Prints OK / WARN / FAIL lines and the exact fix command for anything missing.
set -uo pipefail

echo "== iOS build environment doctor =="
echo "macOS $(sw_vers -productVersion) ($(sw_vers -buildVersion))  arch: $(uname -m)"
echo

# --- Xcode toolchain ---
active="$(xcode-select -p 2>/dev/null || true)"
if printf '%s' "$active" | grep -q "Xcode.app"; then
  echo "OK   Active toolchain: $(xcodebuild -version 2>/dev/null | head -1) [$active]"
else
  echo "FAIL Active toolchain is '$active' (Command Line Tools, not Xcode)."
  if ls -d /Applications/Xcode*.app >/dev/null 2>&1; then
    echo "     Fix: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer && sudo xcodebuild -license accept"
  else
    echo "     Fix: install Xcode first  ->  xcodes install --latest   (needs Apple ID)"
  fi
fi

# --- iOS SDK ---
if xcodebuild -showsdks 2>/dev/null | grep -qi "iphoneos"; then
  echo "OK   iOS SDK: $(xcodebuild -showsdks 2>/dev/null | grep -i 'iphoneos' | head -1 | sed 's/^[[:space:]]*//')"
else
  echo "WARN iOS SDK not detected (resolve the Xcode toolchain above first)."
fi

# --- Simulator runtime ---
if xcrun simctl list runtimes 2>/dev/null | grep -qiE "iOS [0-9]"; then
  echo "OK   iOS Simulator runtime(s):"
  xcrun simctl list runtimes 2>/dev/null | grep -iE "iOS [0-9]" | sed 's/^/       /'
else
  echo "FAIL No iOS Simulator runtime installed."
  echo "     Fix: xcodebuild -downloadPlatform iOS    (~8.5 GB; do NOT 'simctl runtime delete' images)"
fi

# --- An available iPhone simulator ---
iphone="$(xcrun simctl list devices available 2>/dev/null | grep -oE 'iPhone [^(]*' | head -1 | sed 's/ *$//')"
[ -n "$iphone" ] && echo "OK   Bootable iPhone simulator available: $iphone" \
                  || echo "WARN No available iPhone simulator (install a runtime above)."

echo
echo "-- CLI tooling (Homebrew) --"
for t in xcodegen xcbeautify swiftlint swiftformat aria2c fastlane; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "OK   $t -> $(command -v "$t")"
  else
    pkg="$t"; [ "$t" = "aria2c" ] && pkg="aria2"
    echo "WARN $t missing  ->  brew install $pkg"
  fi
done
echo
echo "Doctor finished. Resolve any FAIL lines before building."
