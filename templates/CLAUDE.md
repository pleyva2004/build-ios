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
  ./ui apply --end-turn <<'OPS'
  {"say": "<= 12 words: what changes", "ops": [ ... ]}
  OPS
  ```
  `--end-turn` ends your turn as soon as the app confirms, with `say` as the reply, so
  don't write a summary. Leave it off only if you need more calls this turn.
- If it's rejected, fix the ops from the error text and call once more.
- Never edit `Sources/Runtime/` to change the UI, and never rebuild for a UI change.
- Undo: `./ui undo`. Screenshots only when asked: `./ui shot --small`, then read the file.

## Working with a designer (no technical words)
The person prompting is often a designer who only talks to you. Keep replies to one plain sentence,
never ask them to run commands, and aim for fewer rounds:
- A feeling or open-ended ask → 3 `"options"` in one apply (see the reference). They flip between them
  instantly by saying "next", "previous", "option 2", and "keep".
- Every change must fit the designer's brief at the top of the context. When they state a lasting
  preference, append it to the brief in the same apply.
- These words are handled before they reach you, in about 0.3 s: undo, redo, next, previous, keep,
  option N, select (then they tap an element; "this" means it). If one reaches you anyway, run the
  matching `./ui undo` / `./ui redo` / `./ui next` / `./ui prev` / `./ui keep`.

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

## Graduating to plain SwiftUI
`./ui eject` writes a standalone SwiftUI app (no JSON runtime) to `../<Name>SwiftUI`.
Use it when the user wants "real code" or to hand the design to developers. Re-run it after
more design changes; it never overwrites files edited in the ejected project.

## Reference (node types, props, ops)
@tools/REFERENCE.md
