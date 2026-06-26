---
name: build-ios
description: Scaffold, build, run, and screenshot iOS (SwiftUI) apps entirely from the command line on this Mac — no Xcode GUI required. Use whenever the user wants to create a new iOS/SwiftUI app, build or run an iOS app on the Simulator, check whether the machine is ready for iOS development, or take a Simulator screenshot. Trigger phrases include "/build-ios", "build an iOS app", "make a SwiftUI app", "run it on the simulator", "scaffold an iPhone app", "is my Mac ready for iOS dev".
---

# build-ios

> Command-line iOS development harness. Everything needed to go from nothing to a
> running app on the iOS Simulator without opening Xcode — built and verified on
> Apple Silicon (Xcode 26.x / iOS 26.x), portable to future versions.

## When to use
- Create a new SwiftUI app from a clean, reproducible template.
- Build / run / screenshot an existing app on the iOS Simulator from the terminal.
- Diagnose whether the Mac is set up for iOS development (and get the exact fix commands).

## How it works (3 building blocks)

### 1. Preflight — is the machine ready?
Always run the doctor first; it prints `OK` / `WARN` / `FAIL` with the exact fix command for anything missing (Xcode toolchain, iOS SDK, Simulator runtime, Homebrew tooling):
```bash
bash scripts/doctor.sh
```
If a `FAIL` line appears, run its suggested fix, then re-run the doctor. The common gaps and fixes:
- **Xcode not installed** → `xcodes install --latest` (needs the user's Apple ID; this is interactive — have the **user** run it).
- **Toolchain points at CommandLineTools** → `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer && sudo xcodebuild -license accept` (needs the user's password — user runs it).
- **No Simulator runtime** → `xcodebuild -downloadPlatform iOS` (~8.5 GB; run in the background).

### 2. Scaffold — create a new app
```bash
bash scripts/scaffold.sh <AppName> [parent-dir] [bundle-prefix]
# e.g.  bash scripts/scaffold.sh Notes ~/Code com.pablo
```
Produces a self-contained project: `project.yml` (XcodeGen spec — the source of truth), `Sources/<AppName>App.swift` + `Sources/ContentView.swift`, a bundled `run.sh`, and a generated `<AppName>.xcodeproj`. Non-alphanumerics in the name are stripped for the scheme/bundle id.

### 3. Build + run + screenshot
From inside a scaffolded app folder:
```bash
./run.sh                 # auto-picks a simulator (prefers iPhone 17 Pro)
./run.sh "iPhone Air"    # or name any installed simulator
```
`run.sh` regenerates the project, builds for the generic iOS Simulator destination (piped through `xcbeautify`), boots the simulator, installs + launches the app, and writes `<AppName>-screenshot.png`. **Read that PNG to visually confirm the app rendered.**

## Key facts & gotchas (learned the hard way)
- **Build with `-destination 'generic/platform=iOS Simulator'`**, not a named device. Right after a runtime install, `xcodebuild` often only lists placeholder destinations, so a named `-destination` fails to match. The generic destination always resolves; target the concrete device later via `simctl`.
- **Never `xcrun simctl runtime delete` a runtime image**, even one shown as a `Duplicate`/`Unusable`. Duplicates can share the same backing `.dmg`; deleting one removes the asset and forces a full ~8.5 GB re-download. If a runtime looks stuck, `killall Simulator; xcrun simctl shutdown all` and re-run `xcodebuild -downloadPlatform iOS` instead.
- **Swift macros can fail on a freshly installed Xcode** with `external macro implementation type ... could not be found ...; swift-plugin-server produced malformed response` — this affects `#Preview`, `@Observable`, SwiftData `@Model`, etc., not just previews. The scaffold template is deliberately **macro-free** so headless builds are deterministic from day one. If you add a macro and hit this, it usually clears once Xcode.app has been opened in the GUI once (macOS finishes verifying the bundle and warms the plugin server); `run.sh` also retries the build once.
- **The interactive steps belong to the user.** Installing Xcode (`xcodes install`, Apple ID + 2FA) and `sudo` toolchain config can't be typed by the agent — hand the user the exact command (e.g. via the `! <cmd>` prompt prefix) and wait.
- **Free Apple ID** is enough to build and run on the Simulator. The paid Apple Developer Program ($99/yr) is only needed for App Store / TestFlight / device push.
- Swift 6 language mode: set `SWIFT_VERSION: "6.0"` in `project.yml` (default `5.0` for a frictionless first build), then re-run `xcodegen generate`.

## Useful one-liners
```bash
xcrun simctl list devices available          # bootable simulators
xcrun simctl list runtimes                   # installed iOS runtimes
xcrun simctl io booted screenshot out.png    # screenshot the booted sim
swiftlint ; swiftformat .                     # lint + auto-format
open <AppName>.xcodeproj                      # open in Xcode for visual editing / Previews
```

## Bundled files
@templates/project.yml
@templates/App.swift
@templates/ContentView.swift
@templates/run.sh
@scripts/doctor.sh
@scripts/scaffold.sh
