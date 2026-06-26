# build-ios

A Claude Code **skill** for building iOS (SwiftUI) apps entirely from the command line — no Xcode GUI required. Scaffold a new app, build it, run it on the Simulator, and capture a screenshot, all from the terminal (and drivable by Claude Code).

Built and verified on Apple Silicon with **Xcode 26.x / iOS 26.x**, and designed to stay portable to future versions.

## Requirements
- macOS on Apple Silicon
- **Xcode** (full app, not just Command Line Tools) — install with [`xcodes`](https://github.com/XcodesOrg/xcodes): `brew install xcodes && xcodes install --latest`
- Homebrew tools: `brew install xcodegen xcbeautify swiftlint swiftformat aria2 fastlane`
- An iOS Simulator runtime: `xcodebuild -downloadPlatform iOS`

Run `scripts/doctor.sh` to check all of the above and get the exact fix for anything missing.

## Install as a Claude Code skill
Clone into your personal skills directory:
```bash
git clone <this-repo> ~/.claude/skills/build-ios
```
Then invoke it with `/build-ios`, or just ask Claude to "build an iOS app".

## Usage (standalone)
```bash
# 1. Is this Mac ready?
bash scripts/doctor.sh

# 2. Create a new app
bash scripts/scaffold.sh MyApp ~/Code com.you

# 3. Build + boot Simulator + install + launch + screenshot
cd ~/Code/MyApp && ./run.sh
```

## Layout
```
build-ios/
├── SKILL.md              # skill instructions + gotchas (read this)
├── scripts/
│   ├── doctor.sh         # environment readiness check
│   └── scaffold.sh       # new-app generator (templates + XcodeGen)
└── templates/
    ├── project.yml       # XcodeGen project spec
    ├── App.swift         # @main entry point
    ├── ContentView.swift # starter UI (macro-free for reliable headless builds)
    └── run.sh            # build → boot → install → launch → screenshot
```

## Design notes
- Builds use the **generic iOS Simulator destination** so they resolve even right after a runtime install.
- The template is intentionally **macro-free** (no `#Preview`) so `xcodebuild` never depends on the Swift macro plugin (`swift-plugin-server`), which can be flaky on a freshly installed Xcode. Add `#Preview { ContentView() }` back once your toolchain is warm.
- ⚠️ Never `xcrun simctl runtime delete` a runtime image — duplicates can share one backing asset, and deleting forces an ~8.5 GB re-download.

## License
MIT
