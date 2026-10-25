# Low-latency UI iteration: architecture

**Goal:** shrink the time from "idea for a UI change" to "I can see it on the phone"
from minutes to about a second, so iteration is limited by how fast you can judge
designs, not by tooling. The scope is UI prototypes and demos only: no backend and
no business logic.

## Where the time went
| Step (before) | Typical cost | Why |
|---|---|---|
| Big model explores the codebase | 30–120 s | Finding *where* a change goes takes many tool calls |
| Model rewrites Swift | 5–30 s | Output tokens dominate model latency |
| `xcodegen` + `xcodebuild` | 20–90 s | Compiling for a visual tweak; generic builds also compile two CPU archs |
| boot / install / relaunch + `sleep 3` | 5–20 s | Navigation state lost on every launch |

Those are two separate problems:
1. **Knowing what and where** (model time).
2. **Seeing it** (build time).

Since the apps are generated from scratch, we control the architecture, so both can be fixed by design.

## The design: make the UI data, give every element an address
```
 you / a model ──"make the CTA a teal pill"──▶ ./ui  (validate → atomic write → bump rev)
        ▲                                         │ design/*.json        .live/rev
        │                                         ▼                         │
  .live/status.json ◀── app confirms rev ── Simulator app (LiveStore polls every 150 ms,
  (screen, stack, picked element, errors)       re-renders in place, keeps navigation)
```

### 1. The UI is JSON, rendered at runtime (`Sources/Runtime`)
- **Files:** `design/app.json` (tabs/root), `tokens.json` (colors, fonts, spacing, radius, shadows, named styles) and one `screens/<id>.json` per screen.
- **Renderer:** a macro-free SwiftUI interpreter (`LiveRenderer.swift`) renders the node tree.
  - It covers ~20 node types: stacks, scroll, grid, list/section, text, label, image, button, toggle, textfield, picker, shape, progress, repeat-over-data, custom.
  - Props are flat: padding, frame, colors, fonts, radius, shadow, Liquid Glass.
  - Actions cover navigation, sheets, tabs, demo state and alerts.
  - So a demo app is fully clickable with zero Swift.
- **Hot reload:** the app reads the Mac's project folder directly. The Simulator doesn't sandbox host paths; the path comes from `SIMCTL_CHILD_UI_PROJECT_DIR`, a launch argument, or `#filePath`.
  - On change it re-renders in place and keeps navigation, state and scroll position.
  - Bad JSON keeps the last good design on screen and shows an error banner.
- **Fallback:** a bundled copy of `design/` means the app also runs standalone (device, home-screen launch).

### 2. Every element has a stable id, and the app reports what's on screen
- Ids like `home.hero.cta` are the source address: no searching.
- `.live/status.json`: the app writes the visible screen, nav stack, last applied revision and errors.
- **Pick mode** (`./ui pick`): tap an element in the Simulator and its id lands in `.live/selection.json` — like click-to-source in web builders. "This" stops being ambiguous.
- Repeat rows report their row index, so the model knows to edit the template (every row) or the data (one row).

### 3. Context is pre-packed, so models never explore (`./ui context`)
A compact block, about 1–3k tokens for a demo app. It contains:
- the current screen as a one-line-per-element outline (id, type, props);
- the other screens, when they fit;
- the design tokens and styles;
- a FOCUS line: what "this/it" means (fresh pick > last edited element);
- text→id matches for quoted text in the request;
- the last few edits, so "a bit bigger" has a referent.

In Claude Code a `UserPromptSubmit` hook adds this to every plain prompt and to `/ui`, so the model needs **zero exploratory tool calls**. The hook doesn't depend on the session's working directory, and it includes the text→id matches.

### 4. Edits are tiny, id-addressed ops (`./ui apply`)
- **Ops:** `set`, `nudge`, `insert`, `remove`, `move`, `wrap`, `unwrap`, `replace`, `token`, `data`, `screen`.
- **Size:** an edit is typically 20–80 output tokens instead of a rewritten file, and output tokens dominate model latency.
- **Applying:** ops apply all-or-nothing, with an undo snapshot. Then `./ui` waits for the app to echo the revision ("live on 'home' in 0.21s").
- **`nudge`** makes relative requests deterministic. "Bigger" steps along the design's own type scale or spacing tokens; "bolder" steps the weight; a button gets the next control size.
- **Scope rules** in the prompt map phrasing to an op:
  - "this" → the element;
  - "all cards" → the `card` style;
  - "brand" → a color token.

### 5. The engine catches small-model slips before they cost a round trip
**Auto-fix and report.** The validator mirrors the Swift renderer exactly; a test enforces no drift. It silently fixes:
- prop aliases: `cornerRadius`→`radius`, `color`→`foreground`, `title`→`text`, …;
- type aliases: `VStack`→`vstack`;
- `style:"primary"` on a button → `variant`;
- bare token names → `$token`;
- CSS color names → hex;
- `left`/`right` → `leading`/`trailing`;
- numeric strings; `opacity: 50` → `0.5`.

**Reject with an actionable error**:
- unknown ids, with "did you mean";
- unknown props, with the right name;
- missing tokens, with the op that adds one;
- invented SF Symbol names (checked against the Mac's symbol list);
- dead navigation targets;
- out-of-range numbers (the runtime also clamps them, so a bad value can't crash the app).

`./ui ask` feeds the error back for exactly one corrective retry.

### 6. Model calls are single-shot and small
- **`./ui ask`:** one Claude API call. Defaults: `claude-haiku-5-5`, thinking off, effort low, JSON reply `{"say", "ops"}`. The static prefix (rules + reference) is prompt-cached for 1 h, and `chat` pre-warms the cache.
- **Variants:** `-n 3` runs three parallel calls with different design directions. The first is shown as soon as it lands; `./ui next` flips between them instantly.
- **`/ui` in Claude Code:** a project skill with `model: haiku` and `effort: low`.
  - The context arrives through the `UserPromptSubmit` hook.
  - The model makes exactly one `./ui apply --end-turn` call. On success a `PostToolUse` hook ends the turn (`continue: false`), so there's no second model response. An escalation (`ops: []`) also ends the turn: with `--end-turn` it exits 0 so the same hook can stop the turn with the `ESCALATE:` message (Claude Code ignores `continue: false` from `PostToolUseFailure`). A rejected apply exits 1, which doesn't end the turn, so the model can fix the ops and retry once.
  - A `PreToolUse` hook auto-approves only inert `./ui … <<'OPS'` heredocs, so there are no permission prompts.

### 7. Escalation: a clear line between "data" and "code"
If JSON can't express a request (maps, charts, gestures, custom animation), `ask` exits 3 with `ESCALATE: …`. The model, or you, then:
1. adds `Sources/Components/<Name>.swift`;
2. registers it in `LiveComponents.registry` and `catalog`;
3. rebuilds once;
4. places it with `{"type":"custom","name":…}`.

The app reports its component catalog to the tooling, so models can use registered components and the validator knows them.

### 8. When Swift does change, the build is lean (`run.sh`)
- One CPU arch (`ARCHS=arm64`) instead of two.
- No index store.
- `xcodegen --use-cache`.
- The Simulator boots in parallel with the build.
- `simctl launch --terminate-running-process`.
- It waits for the app's status handshake instead of `sleep 3`.

## Latency budget (measure your own with `./ui stats`)
"Measured" rows come from headless Claude Code 2.1 runs on Haiku against a scaffolded app;
the CLI startup of a fresh `claude -p` process (~2–3 s) is not included in the in-session numbers.

| Path | Model | Write + validate | App confirms | Total |
|---|---|---|---|---|
| `./ui tune` key (no model) | — | ~30 ms | ≤150 ms | **~0.2 s** |
| `./ui apply` (ops given) | — | ~30 ms | ≤150 ms | **~0.2 s** (0.25 s + 0.22 s in CI) |
| `/ui` in Claude Code (Haiku) | ~2 s | ~30 ms | ≤150 ms | **3.0 s measured** (2 turns, ~$0.004) |
| plain prompt, `claude --model haiku` session | ~2.5 s | ~30 ms | ≤150 ms | **3.2 s measured** |
| rejected op → corrected retry (`/ui`) | ~7 s | | | **9.1 s measured** (3 turns) |
| escalation (`/ui`, needs Swift) | ~3 s | — | — | **4.2 s measured** |
| `./ui ask` via the direct API (Haiku, warm cache) | ~0.6–1.5 s | ~30 ms | ≤150 ms | **~1–2 s** (estimate) |
| `./ui ask` via the `claude -p` fallback | ~4.5 s | ~30 ms | ≤150 ms | **5.4 s p50 measured** (incl. CLI startup) |
| new Swift component | — | incremental build | relaunch | ~10–20 s |
| before this change | 30–150 s | — | build + relaunch | **1–3 min** |

## Verification
- `tests/test_ui.py` covers every op, the auto-fixes, validation, undo, the live handshake, hooks, `ask` with a fake model, and a drift test between the Swift renderer, the validator and `REFERENCE.md`.
- `.github/workflows/ios.yml` compiles a scaffolded app on a macOS runner (Xcode 26.6) and runs a Simulator smoke test:
  - launch → app reports a live link;
  - `./ui apply` → the app confirms the revision (0.47 s in CI);
  - an invalid op is rejected;
  - undo restores `design/` byte-for-byte;
  - before/after screenshots are uploaded.
- `./ui eval` measures the model itself. `tools/evals.jsonl` holds 24 natural-language requests: scope, layout, repeat template vs. row data, toolbar insert, follow-ups and must-escalate cases. Each is checked by assertions on the resulting design, and the report gives pass rate, valid-first-try rate and p50/p90 latency per model. On Haiku (through `claude -p`), all 24 produced valid ops on the first try. The first run passed 21/24, which exposed a nudge bug at the top of the radius scale and the undocumented `path` op. After fixing those, and a too-strict check (a larger font token is also "bigger"), it passes 24/24 at a 5.8 s p50 (including CLI startup).
- Live Claude Code runs on a scaffolded app (with a stand-in for the Simulator that confirms renders) checked the integration:
  - the `UserPromptSubmit` context hook;
  - `/ui` switching to Haiku;
  - the `PreToolUse` auto-allow for the heredoc;
  - the turn ending after apply;
  - a rejected op being retried;
  - escalation ending the turn;
  - the `PostToolUse` validator blocking a bad hand edit.

  These runs also found that Claude Code ignores `continue: false` from `PostToolUseFailure` hooks, which is why escalation now ends the turn through `PostToolUse`.

## Not done yet / ideas
- **Physical iPhone hot reload:** serve `design/` over the LAN (needs ATS `NSAllowsLocalNetworking` / IP exceptions and the Local Network permission). Today a device runs the bundled copy.
- **Swift-level hot reload** (InjectionNext) for custom components, instead of a rebuild.
- **Log quick undos** as failure labels and grow the eval set from them.
- **"Eject"** a settled design to handwritten SwiftUI, if a prototype graduates to a real app.
