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
if command -v xcodegen >/dev/null 2>&1; then
  echo "OK   xcodegen -> $(command -v xcodegen)"
else
  echo "FAIL xcodegen missing (run.sh needs it)  ->  brew install xcodegen"
fi
for t in xcbeautify swiftlint swiftformat aria2c fastlane; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "OK   $t -> $(command -v "$t")"
  else
    pkg="$t"; [ "$t" = "aria2c" ] && pkg="aria2"
    echo "WARN $t missing  ->  brew install $pkg"
  fi
done
echo
echo "-- Live-UI fast path (optional, for ./ui ask / ./ui chat) --"
if command -v python3 >/dev/null 2>&1; then
  echo "OK   python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') (./ui core needs 3.9+, the python3 macOS ships)"
else
  echo "FAIL python3 missing  ->  xcode-select --install"
fi
if command -v uv >/dev/null 2>&1; then
  echo "OK   uv -> $(command -v uv) (runs ./ui ask with a current anthropic SDK)"
else
  echo "WARN uv missing  ->  brew install uv   (only needed for ./ui ask outside Claude Code)"
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -d "$HOME/.config/anthropic" ]; then
  echo "OK   Claude API credentials found (./ui ask makes one direct API call)"
else
  echo "WARN no ANTHROPIC_API_KEY — ./ui ask falls back to 'claude -p' (slower); /ui inside Claude Code needs no key"
fi
command -v claude >/dev/null 2>&1 && echo "OK   claude -> $(command -v claude)" || echo "WARN Claude Code CLI not on PATH"
echo
echo "Doctor finished. Resolve any FAIL lines before building."
