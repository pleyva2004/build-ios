---
name: ui
description: Fast UI change on the live prototype — one small-model turn, no codebase reading, no rebuild. Use for any visual, layout, copy, navigation or new-screen change to this app's UI.
argument-hint: <what to change>
model: haiku
effort: low
allowed-tools: Bash(./ui *)
---
Change the live prototype's UI: $ARGUMENTS

The live state (current screen outline, tokens, picked element, recent edits, and
which ids the request mentions) is in the `<ui-context>` block added to this prompt.
If there is no such block, run `./ui context` once first. The node types, props and
ops are in CLAUDE.md (tools/REFERENCE.md). Do not read or search any other files.
Reply with exactly one Bash call and no other text:

./ui apply --end-turn <<'OPS'
{"say": "<= 12 words: what changes", "ops": [ ... ]}
OPS

- "this / it / that" = the FOCUS element above. Use the narrowest op: one element →
  `set` or `nudge`; "all cards/buttons" → its style (`token` on `styles.*`); brand or
  theme → a color `token`; bigger / smaller / more / less → `nudge`.
- If the call is rejected, fix the ops using the error text and call once more.
- If JSON can't express it (maps, charts, gestures, animations), call it with
  `"ops": []` and say which Swift component would be needed.
