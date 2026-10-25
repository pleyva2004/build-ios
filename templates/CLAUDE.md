# __APP_NAME__ — live UI prototype

This is a UI prototype with no backend or business logic. Its UI is **data, not
Swift**: `design/*.json`, rendered by `Sources/Runtime`. The app running in the
Simulator hot-reloads those files in ~0.2 s, so UI changes never need a rebuild.

## How to change the UI (all models)
- Don't read the codebase. The live state (current screen outline, tokens, the
  element the user picked, recent edits) arrives in a `<ui-context>` block with
  each prompt. If it's missing, run `./ui context` once.
- Make the change with ONE call. It validates, applies atomically, and waits for the app:
  ```
  ./ui apply <<'OPS'
  {"say": "<= 12 words: what changes", "ops": [ ... ]}
  OPS
  ```
- If it's rejected, fix the ops from the error text and call once more.
- Never edit `Sources/Runtime/` to change the UI, and never rebuild for a UI change.
- Undo: `./ui undo`. Screenshots only when asked: `./ui shot --small`, then read the file.

## When JSON can't express it (escalation)
For maps, charts, gestures, custom drawing or animation:
1. Add a SwiftUI view in `Sources/Components/<Name>.swift`.
2. Register it in `LiveComponents.registry` and `catalog` (Components.swift).
3. Run `./run.sh` once (the only time Swift is compiled).
4. Place it with a `{"type":"custom","name":"<Name>"}` node via `./ui apply`.

## Fast loops
- `/ui <change>` — one Haiku turn: context injected, one apply, turn ends.
- `claude --model haiku` in this folder — a whole design session on the fast model.
- Outside Claude Code: `./ui ask "<change>"` (one API call), `./ui ask -n 3 "<idea>"`
  then `./ui next`, `./ui chat`, `./ui pick` (tap an element), `./ui tune` (no model:
  keys nudge size, padding, color), `./ui stats` (latency).

## Reference (node types, props, ops)
@tools/REFERENCE.md
