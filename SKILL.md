---
name: build-ios
description: Scaffold, run, and iterate on iOS (SwiftUI) UI prototypes from the command line on this Mac, with sub-second hot reload of UI changes and no Xcode GUI. Use whenever the user wants to create a new iOS/SwiftUI app or demo, change the UI of one, run it on the Simulator, take a Simulator screenshot, or check whether the Mac is ready for iOS development. Trigger phrases include "/build-ios", "build an iOS app", "make a SwiftUI app", "prototype an app", "change the UI", "run it on the simulator", "is my Mac ready for iOS dev".
---

# build-ios

> Command-line iOS UI prototyping. Scaffolded apps render their UI from JSON
> (`design/*.json`). The app in the Simulator hot-reloads it in ~0.2 s, so a UI
> change is one small edit: no codebase reading, no rebuild, and small fast
> models can do it. Built for Apple Silicon, Xcode 26+, iOS 26+.

## The loop (read this first)
1. **Preflight** (once per Mac): `bash scripts/doctor.sh`. Fix any `FAIL` with the printed command.
2. **Scaffold**: `bash scripts/scaffold.sh <AppName> [parent-dir] [bundle-prefix]`
3. **Launch once**: `cd <app> && ./run.sh` builds, boots the Simulator, launches and writes `.live/shot.png`.
4. **Iterate with no rebuild.** Inside the app folder, every UI change is ONE call:
   ```
   ./ui apply <<'OPS'
   {"say": "what changes", "ops": [{"op": "set", "id": "home.hero.title", "props": {"size": 28}}]}
   OPS
   ```
   It validates, writes atomically, waits until the running app confirms the new
   revision (~0.2 s), and prints what changed. `./ui undo` reverts.

**Never explore the app's code to make a UI change.** Get the whole picture with
`./ui context`: the current screen's element tree with ids, design tokens, the
element the user picked, and recent edits. The app's own `CLAUDE.md` holds the
ops reference (`tools/REFERENCE.md`).

## Which path?
| The change | Path | Typical latency |
|---|---|---|
| Colors, copy, spacing, sizes, layout, new screens, navigation, demo data | **Fast path**: `./ui apply` (or `/ui`, `./ui ask`) | ~1–3 s with a small model; ~0.2 s to render |
| Something JSON can't express (maps, charts, gestures, custom drawing/animation) | **Compile path**: add `Sources/Components/<Name>.swift`, register it in `LiveComponents.registry` + `catalog`, `./run.sh`, then place it with a `custom` node | one incremental build (~10–20 s) |
| Changes to `Sources/Runtime/` | Only to fix a runtime bug, never for UI work | rebuild |

`./ui ask` exits with code 3 (`ESCALATE: …`) when a request needs the compile path.

## Fast lanes for the user
- **In Claude Code**: `/ui <change>` (a project skill in every scaffolded app). It runs one Haiku turn with the live context already injected, makes one `./ui apply --end-turn`, and a hook ends the turn. For long sessions, start `claude --model haiku` in the app folder.
- **In a terminal**: `./ui ask "<change>"` is one direct Claude API call (needs `ANTHROPIC_API_KEY` + `uv`). `./ui ask -n 3 "<idea>"` builds 3 variants in parallel; switch with `./ui next` / `prev` / `keep`. `./ui chat` keeps the client warm.
- **No model at all**: `./ui pick` (tap an element in the Simulator → its id), then `./ui tune` (single keys nudge size/padding/spacing/radius/weight, cycle colors and styles).
- **Measure**: `./ui stats` prints p50/p90 for model time, write time and render confirmation. `./ui eval` runs `tools/evals.jsonl`, 24 requests with assertions on the resulting design, and reports pass rate, valid-first-try rate and latency. It makes one model call per case.

## Key facts & gotchas
- **Hot reload needs the project folder readable by the Simulator.** Keep projects out of `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive, which macOS privacy protection may block. `./ui status` shows `source` (env/defaults/source = live). If it says the app hasn't reported while the app is on screen, the app is running its bundled `design/` copy and hot reload is off.
- **Build with `-destination 'generic/platform=iOS Simulator'` plus `ARCHS=arm64`.** The generic destination always resolves, even right after a runtime install, but it builds every simulator arch unless `ARCHS` is set. `run.sh` does both, skips indexing, reuses the XcodeGen cache, and boots the Simulator while it builds.
- **Never `xcrun simctl runtime delete` a runtime image**, even one shown as `Duplicate`/`Unusable`. Duplicates can share one backing `.dmg`, so deleting one forces a ~8.5 GB re-download. If a runtime looks stuck: `killall Simulator; xcrun simctl shutdown all`, then re-run `xcodebuild -downloadPlatform iOS`.
- **The templates are macro-free** (no `#Preview`, `@Observable`, `@Entry`). Swift macros can fail on a freshly installed Xcode (`swift-plugin-server produced malformed response`). The runtime uses `ObservableObject`. Keep `SWIFT_VERSION: "5.0"`: the runtime relies on Swift 5 concurrency rules.
- **The interactive steps belong to the user.** `xcodes install` (Apple ID + 2FA) and `sudo xcode-select …` can't be typed by the agent. Hand the user the exact command and wait.
- **A free Apple ID is enough** for the Simulator. The $99/yr program is only needed for App Store, TestFlight or device push.
- **Physical iPhone**: the app runs from its bundled `design/` copy (no hot reload yet). Re-run `./run.sh` after design changes you want baked in.

## Useful one-liners
```bash
./ui context                  # what a model needs to know (current screen, ids, tokens, focus)
./ui validate                 # check design/*.json
./ui shot --small             # Simulator screenshot (+ ~900px JPEG)
./run.sh --launch             # relaunch without building
xcrun simctl list devices available
```

## Bundled files
@templates/tools/REFERENCE.md
@scripts/doctor.sh
@scripts/scaffold.sh
