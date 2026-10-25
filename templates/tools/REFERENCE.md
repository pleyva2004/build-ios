# Live UI reference (design/*.json + ./ui ops)

The app's whole UI is JSON in `design/`. The Simulator app hot-reloads it in ~0.2 s — no
Swift, no rebuild. Change it with **ops**: small JSON edits addressed by element `id`.

## Files
- `design/app.json` — `{"name", "tabs":[{"id","title","symbol","screen"}]}` or `{"root":"<screen>"}`; optional `"state":{key: initial}`, `"tint":"$brand"`, `"colorScheme":"light|dark|system"`.
- `design/tokens.json` — `colors` (+ `colorsDark` overrides), `fonts`, `spacing`, `radius`, `shadows`, `styles` (named prop bundles, e.g. `card`).
- `design/screens/<id>.json` — `{"id","title","titleDisplay":"large|inline|hidden","background","toolbar":[nodes],"data":{...},"body":<node>}`.

## Nodes
`{"type": T, "id": "screen.part", ...props, "children": [...]}` — ids are unique and stable.

| type | own props |
|---|---|
| `vstack` `hstack` `zstack` | `spacing`, `align` (vstack: leading/center/trailing · hstack: top/center/bottom · zstack: center/top/bottom/leading/trailing/topLeading/topTrailing/bottomLeading/bottomTrailing) |
| `scroll` | `axis` vertical/horizontal, `spacing`, `align` — its children are stacked |
| `grid` | `columns` (int), `spacing` |
| `list` | `listStyle` insetGrouped/plain/grouped/inset — children: `section`s or rows |
| `section` | `header`, `footer` |
| `text` | `text` |
| `label` | `text`, `symbol` |
| `image` | `symbol` (SF Symbol name) or `url` or `asset`; `fit` fill/fit, `aspect` (w/h) |
| `button` | `text` and/or `symbol` (or `children`), `variant` primary/secondary/plain/borderless/destructive/glass/glassProminent, `action`, `controlSize` mini/small/regular/large/extraLarge. With a variant, `tint` colors it and `radius` shapes it |
| `toggle` | `text`, `bind` (state key) |
| `textfield` | `placeholder`, `bind`, `secure`, `keyboard` default/email/number/decimal/phone/url |
| `picker` | `text`, `bind`, `options` [strings], `pickerStyle` segmented/menu/wheel/inline |
| `shape` | `shape` rect/rounded/circle/capsule, `fill` |
| `progress` | `value` 0–1 (omit = spinner) |
| `spacer` · `divider` | — |
| `repeat` | `items` (`"data.<key>"`), `template` (one node). In the template: `{{field}}`, `{{index}}`, `{{number}}` |
| `custom` | `name` (a Swift view in Sources/Components), any props |

**Props for any node.**
layout: `padding` (n · `$space` · `{"horizontal","vertical","top","bottom","leading","trailing"}`), `width`, `height`, `maxWidth`/`maxHeight` (n or `"infinity"`), `minWidth`, `minHeight`, `frameAlign` (where content sits inside its frame), `offset` `{"x","y"}`, `ignoreSafeArea`
look: `background` (color or `{"gradient":[c1,c2],"direction":"down|right|diagonal"}`), `foreground`, `tint`, `opacity` (0–1), `radius`, `border` `{"color","width"}`, `shadow` (`$token`), `glass` (true/"clear" — Liquid Glass)
text (children inherit): `font` (`$token`), `size`, `weight` (light…black), `design` (rounded/serif/monospaced), `textAlign`, `lines`, `italic`
behavior: `onTap` (action), `visibleIf` (`"key"` · `"!key"` · `"key==value"`), `disabled`, `style` (a `tokens.styles` bundle; the node's own props win)

**Values.** Colors: `"$token"` (preferred), `"#RRGGBB"`, or system names `blue red green orange pink purple teal indigo yellow mint gray black white clear`. `"primary"`/`"secondary"` are the system text colors, not brand colors. Numbers may be `$spacing`/`$radius` tokens. Strings may contain `{{key}}` (repeat item, nav `params`, screen `data`, then `state`).
**Actions** (`action`/`onTap`, one object or a list): `{"navigate":"<screen>","params":{...}}` · `{"sheet":"<screen>"}` · `{"dismiss":true}` · `{"tab":"<tab id>"}` · `{"toggle":"<key>"}` · `{"set":{"<key>":v}}` · `{"url":"https://…"}` · `{"alert":{"title","message"}}`

## Ops — `./ui apply <<'OPS' … OPS` (one op or a list; applied all-or-nothing)
| op | shape |
|---|---|
| set props (`null` deletes) | `{"op":"set","id":ID,"props":{...}}` — may change `type` (vstack→hstack). On a screen id: `title`, `background`, `titleDisplay` |
| relative change | `{"op":"nudge","id":ID,"prop":"size\|padding\|spacing\|radius\|weight\|opacity","by":1}` (by −1 = smaller; steps along the design's scales) |
| insert | `{"op":"insert","after":ID,"node":{...}}` · `"before":ID` · `"parent":ID` (+`"index":n`) — toolbar: `"parent":"<screen>.toolbar"` |
| remove | `{"op":"remove","id":ID}` or `"ids":[...]` |
| move | `{"op":"move","id":ID,"after"/"before"/"parent":ID}` |
| wrap siblings | `{"op":"wrap","ids":[ID,...],"node":{"type":"hstack",...}}` · `unwrap`: `{"op":"unwrap","id":ID}` |
| replace subtree | `{"op":"replace","id":ID,"node":{...}}` |
| tokens & styles | `{"op":"token","path":"colors.brand","value":"#0EA5E9"}` · `"path":"styles.card.padding"` |
| demo data | `{"op":"data","screen":"home","path":"stories.0.title","value":"…"}` (`"stories.-"` appends) |
| new screen | `{"op":"screen","id":"settings","spec":{"title":"Settings","body":{...}}}` (`"spec": null` deletes it) |
| app.json (tabs, state, tint) | `{"op":"path","file":"app","path":"tabs.0.symbol","value":"house.fill"}` · `"path":"state.plan"` · `"tabs.-"` appends a tab |

New nodes may omit `id`. **Scope:** "this/it/that" → the focused element (`set`/`nudge`);
"all cards / every button" → its **style** (`token` `styles.<name>.…`); "brand / theme /
everywhere" → a **token**. Edit a `repeat` **template** to change every row; edit `data`
to change one row's content. Relative words (bigger, a bit more, less, again) → `nudge`.

## Layout cookbook
- side by side → `wrap` the two siblings in `hstack` (if one sits inside a stack, wrap that stack)
- stacked / below → `move` after, or `wrap` in `vstack`
- center a text → `set` `maxWidth:"infinity"`, `frameAlign:"center"`, `textAlign:"center"`
- push to the right → `insert` a `spacer` before it (inside an hstack)
- full width → `maxWidth:"infinity"` · fixed square → `width` + `height`
- badge / overlay → `wrap` in `zstack` with `align:"topTrailing"`
- equal columns → `grid` · horizontal carousel → `scroll` with `axis:"horizontal"`
- more space between items → `nudge` the parent's `spacing`; inside a box → its `padding`
- card look → `style:"card"` · pill button → `radius:"$pill"`
- Not possible here (answer with no ops and say so): animations, swipe actions, context menus, maps, charts, search bars, sheet heights. Those need a Swift component in Sources/Components (`custom`).

## Examples (ids starting `ex.` are illustrations — never use them)
- focus `ex.cta`, "pill-shaped and teal" → `{"op":"set","id":"ex.cta","props":{"radius":"$pill","tint":"teal"}}`
- "warmer brand color" → `{"op":"token","path":"colors.brand","value":"#F97316"}`
- focus `ex.name`, "make this blue" → `{"op":"set","id":"ex.name","props":{"foreground":"blue"}}` (one element, not the token)
- "bigger" (last edit nudged `ex.title` size) → `{"op":"nudge","id":"ex.title","prop":"size","by":1}`
- "put the avatar next to the name" (`ex.name` is inside `ex.info`) → `{"op":"wrap","ids":["ex.avatar","ex.info"],"node":{"type":"hstack","spacing":"$sm","align":"center"}}`
- "more breathing room in all cards" → `{"op":"token","path":"styles.card.padding","value":"$lg"}` (a style, not each card)
- "add a subtitle under the title" → `{"op":"insert","after":"ex.title","node":{"type":"text","text":"Your week at a glance","foreground":"$textSecondary"}}`
- "rename the second story" → `{"op":"data","screen":"ex","path":"stories.1.title","value":"Field notes"}`
- "settings screen opened from a gear" → `[{"op":"screen","id":"settings","spec":{"title":"Settings","body":{"type":"list","children":[{"type":"section","header":"General","children":[{"type":"toggle","text":"Notifications","bind":"notify"}]}]}}},{"op":"insert","parent":"ex.toolbar","node":{"type":"button","symbol":"gearshape","action":{"sheet":"settings"}}}]`
- "add a map" → `{"say":"Maps need a Swift component (custom) — not possible in JSON","ops":[]}`
