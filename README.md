# build-ios

A Claude Code **skill** for prototyping iOS (SwiftUI) app UIs from the command line, with no Xcode GUI.

Scaffolded apps render their UI from JSON, and the app running in the Simulator hot-reloads
it in about 0.2 seconds. A UI change is therefore one small, validated edit addressed by
element id. There is no codebase to read and nothing to rebuild, so small, fast models
(Claude Haiku) can make changes in about 1–3 s instead of minutes.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

Built for Apple Silicon with **Xcode 26+ / iOS 26+**.

## Requirements
- macOS on Apple Silicon
- **Xcode** (the full app) — `brew install xcodes && xcodes install --latest`
- `brew install xcodegen xcbeautify` (and `uv` for `./ui ask` outside Claude Code)
- An iOS Simulator runtime: `xcodebuild -downloadPlatform iOS`

Run `scripts/doctor.sh` to check everything and get the exact fix for anything missing.

## Install as a Claude Code skill
```bash
git clone <this-repo> ~/.claude/skills/build-ios
```
Then use `/build-ios`, or ask Claude to "prototype an iOS app".

## Usage
```bash
bash scripts/scaffold.sh MyApp ~/Code com.you    # new live-UI app
cd ~/Code/MyApp && ./run.sh                      # build + launch once

# from here on, UI changes hot-reload with no rebuild:
./ui ask "make the main button a teal pill"      # one fast model call
./ui ask -n 3 "make the home screen feel premium" && ./ui next
./ui pick && ./ui tune                           # tap an element, nudge it with keys
./ui undo
./ui eval                                        # model accuracy + latency on 24 sample requests
claude --model haiku                             # then: /ui add a settings screen
```

## Layout
```
build-ios/
├── SKILL.md                 # skill instructions (the loop, which path, gotchas)
├── docs/ARCHITECTURE.md     # why it's fast; latency budget
├── scripts/
│   ├── doctor.sh            # environment readiness check
│   └── scaffold.sh          # new-app generator
├── templates/               # copied into every app
│   ├── Runtime/*.swift      # live-UI engine: JSON → SwiftUI, hot reload, pick mode
│   ├── Components/          # escape hatch: native SwiftUI views usable from JSON
│   ├── design/              # starter UI: app.json, tokens.json, screens/*.json
│   ├── ui                   # CLI: context / apply / undo / pick / ask / tune / stats / hook
│   ├── tools/REFERENCE.md   # node + ops reference (what models read)
│   ├── CLAUDE.md, claude/   # per-app Claude Code rules, /ui skill, hooks
│   ├── run.sh, project.yml  # lean build + launch; XcodeGen spec
│   └── App.swift
├── tests/test_ui.py         # engine tests + Swift/validator drift test
└── .github/workflows/ios.yml# compiles the template + Simulator hot-reload smoke test
```

## License
MIT
