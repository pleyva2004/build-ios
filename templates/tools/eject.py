#!/usr/bin/env python3
"""eject.py — turns a live-UI prototype into a standalone SwiftUI app.

Reads design/*.json (the files the live runtime renders) and writes plain
SwiftUI source: one view per screen, the design tokens as Swift constants, an
observable app state, and a router for tabs, pushes and sheets. The result has
no JSON interpreter. Each node is translated with the same modifier order as
Sources/Runtime/LiveRenderer.swift, so the app looks like the prototype.

Use it through the CLI:  ./ui eject [--out DIR] [--force]
Python 3.9+, standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Optional

GENERATOR = "build-ios eject 1"
MANIFEST = ".eject.json"

SWIFT_KEYWORDS = {
    "associatedtype", "class", "deinit", "enum", "extension", "fileprivate", "func", "import", "init",
    "inout", "internal", "let", "open", "operator", "private", "precedencegroup", "protocol", "public",
    "rethrows", "static", "struct", "subscript", "typealias", "var", "break", "case", "catch", "continue",
    "default", "defer", "do", "else", "fallthrough", "for", "guard", "if", "in", "repeat", "return",
    "throw", "switch", "where", "while", "Any", "as", "await", "false", "is", "nil", "self", "Self",
    "super", "throws", "true", "try", "some", "any", "Type", "Protocol", "async",
}
# Names a generated screen member must not take (View requirements, injected objects).
SCREEN_RESERVED = {"body", "state", "router", "openURL", "item", "index", "previews", "id"}
# Color statics SwiftUI already defines; a token with one of these names gets a prefix.
COLOR_STATICS = {
    "blue", "red", "green", "orange", "pink", "purple", "teal", "indigo", "yellow", "mint", "cyan",
    "brown", "gray", "black", "white", "clear", "primary", "secondary", "accentColor", "init",
}

NAMED_COLORS = {
    "blue": "Color.blue", "red": "Color.red", "green": "Color.green", "orange": "Color.orange",
    "pink": "Color.pink", "purple": "Color.purple", "teal": "Color.teal", "indigo": "Color.indigo",
    "yellow": "Color.yellow", "mint": "Color.mint", "cyan": "Color.cyan", "brown": "Color.brown",
    "gray": "Color.gray", "grey": "Color.gray", "black": "Color.black", "white": "Color.white",
    "clear": "Color.clear", "primary": "Color.primary", "label": "Color.primary",
    "secondary": "Color.secondary", "secondaryLabel": "Color.secondary",
    "accent": "Color.accentColor", "accentColor": "Color.accentColor",
    "systemBackground": "Color(uiColor: .systemBackground)", "background": "Color(uiColor: .systemBackground)",
    "secondarySystemBackground": "Color(uiColor: .secondarySystemBackground)",
    "systemGroupedBackground": "Color(uiColor: .systemGroupedBackground)",
    "groupedBackground": "Color(uiColor: .systemGroupedBackground)",
    "separator": "Color(uiColor: .separator)",
}
WEIGHTS = {"ultraLight", "thin", "light", "regular", "medium", "semibold", "bold", "heavy", "black"}
DESIGNS = {"default": ".default", "rounded": ".rounded", "serif": ".serif", "monospaced": ".monospaced",
           "mono": ".monospaced"}
TEXT_STYLES = {"largeTitle", "title", "title2", "title3", "headline", "subheadline", "body", "callout",
               "footnote", "caption", "caption2"}
H_ALIGN = {"leading": ".leading", "trailing": ".trailing"}
V_ALIGN = {"top": ".top", "bottom": ".bottom", "firstTextBaseline": ".firstTextBaseline",
           "baseline": ".firstTextBaseline"}
ALIGN_9 = {"center": ".center", "top": ".top", "bottom": ".bottom", "leading": ".leading", "trailing": ".trailing",
           "topLeading": ".topLeading", "topTrailing": ".topTrailing", "bottomLeading": ".bottomLeading",
           "bottomTrailing": ".bottomTrailing"}
CONTAINERS = {"vstack", "hstack", "zstack", "scroll", "grid", "list", "section", "repeat"}
SYSTEM_BUTTON_VARIANTS = {"primary", "secondary", "glass", "glassProminent"}
BUTTON_VARIANTS = SYSTEM_BUTTON_VARIANTS | {"plain", "borderless", "destructive"}
BUTTON_STYLES = {"primary": ".borderedProminent", "secondary": ".bordered", "plain": ".plain",
                 "borderless": ".borderless", "glass": ".glass", "glassProminent": ".glassProminent"}
CONTROL_SIZES = {"mini", "small", "regular", "large", "extraLarge"}
KEYBOARDS = {
    "email": [".keyboardType(.emailAddress)", ".textInputAutocapitalization(.never)"],
    "number": [".keyboardType(.numberPad)"],
    "decimal": [".keyboardType(.decimalPad)"],
    "phone": [".keyboardType(.phonePad)"],
    "url": [".keyboardType(.URL)", ".textInputAutocapitalization(.never)"],
}
# Props this generator translates (the drift test compares this with the CLI's vocabulary).
HANDLED_PROPS = {
    "padding", "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight", "frameAlign", "offset",
    "ignoreSafeArea", "background", "foreground", "tint", "opacity", "radius", "border", "shadow", "glass",
    "font", "size", "weight", "design", "textAlign", "lines", "italic", "onTap", "visibleIf", "disabled",
    "style", "placement", "spacing", "align", "axis", "columns", "listStyle", "header", "footer", "text",
    "symbol", "asset", "url", "fit", "aspect", "symbolMode", "variant", "action", "controlSize", "bind",
    "placeholder", "secure", "keyboard", "fieldStyle", "options", "pickerStyle", "shape", "fill", "value",
    "minLength", "items", "name",
}
# "@" and ":" too: LocalizedStringKey markdown turns emails and URLs into tinted links.
MARKDOWN_CHARS = set("*_~`[]%\\<@:")
IND = "    "


# --------------------------------------------------------------------------
# Small helpers: names, literals, the runtime's loose JSON rules
# --------------------------------------------------------------------------


def words(s: str) -> list:
    return [w for w in re.split(r"[^A-Za-z0-9]+", str(s)) if w]


def lower_camel(s: str, fallback: str = "value") -> str:
    ws = words(s)
    if not ws:
        return fallback
    out = ws[0][0].lower() + ws[0][1:] + "".join(w[0].upper() + w[1:] for w in ws[1:])
    if out[0].isdigit():
        out = "n" + out
    if out in SWIFT_KEYWORDS:
        out += "Value"
    return out


def upper_camel(s: str, fallback: str = "Item") -> str:
    ws = words(s)
    if not ws:
        return fallback
    out = "".join(w[0].upper() + w[1:] for w in ws)
    if out[0].isdigit():
        out = "N" + out
    if out in SWIFT_KEYWORDS:
        out += "Value"
    return out


def singular(s: str) -> str:
    if s.endswith("ies") and len(s) > 4:
        return s[:-3] + "y"
    if s.endswith("sses") or s.endswith("shes") or s.endswith("ches") or s.endswith("xes"):
        return s[:-2]
    if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        return s[:-1]
    return s


class Names:
    """Hands out unique identifiers within one Swift scope."""

    def __init__(self, taken=()):
        self.taken = set(taken)

    def get(self, base: str) -> str:
        name, n = base, 2
        while name in self.taken:
            name = f"{base}{n}"
            n += 1
        self.taken.add(name)
        return name


def swift_str(s: str) -> str:
    out = (s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
           .replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{out}"'


def num(v: float) -> str:
    v = float(v)
    if v.is_integer():
        return str(int(v))
    return repr(round(v, 6))


def rt_string(v) -> Optional[str]:
    """LiveJSON.string: strings as-is, bools as true/false, whole numbers without .0."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        d = float(v)
        if not math.isfinite(d):
            return None
        if d == round(d) and abs(d) < 1e15:
            return str(int(d))
        return repr(d)
    return None


def rt_bool(v) -> Optional[bool]:
    """LiveJSON.bool: unknown non-empty words count as true (like the runtime)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0", ""):
            return False
        return True
    return None


def rt_double(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def rt_int(v) -> Optional[int]:
    d = rt_double(v)
    if d is None or not math.isfinite(d) or abs(d) >= 1_000_000:
        return None
    return int(d)


def lookup(root, path: str):
    """LiveJSON.lookup: dotted path through dicts and list indexes."""
    cur = root
    for part in path.split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def split_template(s: str) -> list:
    """Splits "a {{key}} b" into literal and key parts, like LiveJSON.interpolate."""
    parts, rest = [], s
    while True:
        i = rest.find("{{")
        if i < 0:
            parts.append(("lit", rest))
            break
        j = rest.find("}}", i + 2)
        if j < 0:
            parts.append(("lit", rest))
            break
        parts.append(("lit", rest[:i]))
        parts.append(("key", rest[i + 2:j].strip()))
        rest = rest[j + 2:]
    return [p for p in parts if not (p[0] == "lit" and p[1] == "")]


def flat_actions(v) -> list:
    if isinstance(v, list):
        out = []
        for a in v:
            out.extend(flat_actions(a))
        return out
    return [v] if isinstance(v, dict) else []


def indent_block(text: str, level: int) -> list:
    return [(IND * level + line) if line else "" for line in text.split("\n")]


# --------------------------------------------------------------------------
# Values: text, numbers, colors
# --------------------------------------------------------------------------


class Text:
    """A text value: either static (known now) or a Swift String expression."""

    def __init__(self, static: Optional[str], swift: str, safe: bool, string_only: bool):
        self.static = static
        self.swift = swift          # a Swift expression of type String
        self.safe = safe            # literal parts have no markdown / format characters
        self.string_only = string_only  # every interpolated value is a String

    def text_view(self) -> str:
        """Text(...) that renders exactly like the runtime's Text(String)."""
        if self.static is not None:
            return f"Text({self.swift})" if self.safe else f"Text(verbatim: {self.swift})"
        if self.swift.startswith('"'):
            return f"Text({self.swift})" if (self.safe and self.string_only) else f"Text(verbatim: {self.swift})"
        return f"Text({self.swift})"

    def title(self) -> str:
        """A title argument (Label, Toggle, Button, ...) shown verbatim."""
        if self.swift.startswith('"') and not (self.safe and (self.static is not None or self.string_only)):
            return f"{self.swift} as String"
        return self.swift


class Num:
    def __init__(self, expr: str, value: float):
        self.expr = expr
        self.value = value


class Scope:
    """What {{keys}} can see: the repeat item, the screen's params and data, app state."""

    def __init__(self, screen: "ScreenGen", item: Optional[str] = None, fields: Optional[dict] = None,
                 index: bool = False):
        self.screen = screen
        self.item = item
        self.fields = fields or {}
        self.index = index


# --------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------


class Ejector:
    def __init__(self, design_dir: Path, name: str, bundle_id: str):
        self.dir = Path(design_dir)
        self.name = name
        self.app_type = upper_camel(name, "App")
        self.bundle_id = bundle_id
        self.app = self.read(self.dir / "app.json")
        tokens_path = self.dir / "tokens.json"
        self.tokens = self.read(tokens_path) if tokens_path.exists() else {}
        self.screens = {}
        for f in sorted((self.dir / "screens").glob("*.json")):
            if f.name.startswith("."):
                continue
            js = self.read(f)
            sid = js.get("id") if isinstance(js.get("id"), str) else f.stem
            self.screens[sid] = js
        self.todos = []
        self.tok = lambda k: self.tokens.get(k) if isinstance(self.tokens.get(k), dict) else {}
        self.colors, self.colors_dark = self.tok("colors"), self.tok("colorsDark")
        self.fonts, self.spacing, self.radius = self.tok("fonts"), self.tok("spacing"), self.tok("radius")
        self.shadows, self.styles = self.tok("shadows"), self.tok("styles")
        self.screen_types = {}
        names = Names({"RootView", "Router", "Route", "AppTab", "AppState", "Theme", "ThemeShadow", "RemoteImage",
                       "EjectPlaceholder", self.app_type + "App"})
        for sid in self.screens:
            self.screen_types[sid] = names.get(upper_camel(sid, "Screen") + "Screen")
        self.type_names = names
        self.build_theme_names()
        self.tabs = self.read_tabs()
        self.collect_routes()
        self.build_state()

    @staticmethod
    def read(path: Path):
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: top level must be an object")
        return data

    def todo(self, where: str, message: str) -> str:
        self.todos.append(f"{where}: {message}")
        return f"// TODO(eject): {message}"

    # -- tokens -------------------------------------------------------------

    def build_theme_names(self):
        n = Names(COLOR_STATICS | SWIFT_KEYWORDS)
        self.color_names = {}
        for key in list(self.colors) + [k for k in self.colors_dark if k not in self.colors]:
            base = lower_camel(key, "color")
            if base in COLOR_STATICS:
                base = "theme" + upper_camel(key)
            self.color_names[key] = n.get(base)
        self.font_names = self.group_names(self.fonts)
        self.spacing_names = self.group_names(self.spacing)
        self.radius_names = self.group_names(self.radius)
        self.shadow_names = self.group_names(self.shadows)

    @staticmethod
    def group_names(group: dict) -> dict:
        n = Names(SWIFT_KEYWORDS)
        return {k: n.get(lower_camel(k, "token")) for k in group}

    def token_color(self, value, dark: bool, depth: int = 0) -> Optional[str]:
        """Static Swift Color for a token value, resolving $refs like LiveTheme.color."""
        s = rt_string(value)
        if s is None or depth > 6:
            return None
        s = s.strip()
        if s.startswith("$"):
            name = s[1:]
            if dark and name in self.colors_dark:
                return self.token_color(self.colors_dark[name], dark, depth + 1)
            if name in self.colors:
                return self.token_color(self.colors[name], dark, depth + 1)
            return None
        if s.startswith("#"):
            return hex_color(s)
        return NAMED_COLORS.get(s)

    def resolve_number(self, value, group: Optional[dict], allow_inf: bool = False, depth: int = 0) -> Optional[float]:
        s = rt_string(value)
        if s is None or depth > 6:
            return None
        t = s.strip()
        if t.startswith("$"):
            name = t[1:]
            for g in ([group] if group is not None else []) + [self.spacing, self.radius]:
                if name in g:
                    return self.resolve_number(g[name], None, allow_inf, depth + 1)
            return None
        if t in ("infinity", "inf", "max"):
            return math.inf if allow_inf else None
        d = rt_double(t)
        if d is None or not math.isfinite(d) or abs(d) > 100_000:
            return None
        return d

    def font_spec(self, props: dict) -> tuple:
        """(size, weight, design, style) the way LiveTheme.font merges a font spec
        (token chain, text style name or object) with the node's own size/weight/design."""
        size = weight = design = style = None
        spec = props.get("font")
        depth = 0
        while isinstance(spec, (str, int, float)) and not isinstance(spec, bool) and depth < 4:
            depth += 1
            s = rt_string(spec) or ""
            if s.startswith("$"):
                spec = self.fonts.get(s[1:])
            else:
                style, spec = s, None
        if isinstance(spec, dict):
            size = self.resolve_number(spec.get("size"), None)
            weight = rt_string(spec.get("weight"))
            design = rt_string(spec.get("design"))
            if isinstance(spec.get("style"), str):
                style = spec["style"]
        own_size = self.resolve_number(props.get("size"), None)
        if own_size is not None:
            size = own_size
        if rt_string(props.get("weight")) is not None:
            weight = rt_string(props.get("weight"))
        if rt_string(props.get("design")) is not None:
            design = rt_string(props.get("design"))
        return size, weight, design, style

    @staticmethod
    def font_expr(size, weight, design, style) -> Optional[str]:
        w = f".{weight}" if weight in WEIGHTS else None
        d = DESIGNS.get(design or "")
        if size is not None:
            args = [f"size: {num(min(max(size, 1), 400))}"]
            if w:
                args.append(f"weight: {w}")
            if d:
                args.append(f"design: {d}")
            return f"Font.system({', '.join(args)})"
        if style in TEXT_STYLES:
            if not w and not d:
                return f"Font.{style}"
            args = [f".{style}"]
            if d:
                args.append(f"design: {d}")
            if w:
                args.append(f"weight: {w}")
            return f"Font.system({', '.join(args)})"
        return None

    def shadow_expr(self, spec: dict) -> str:
        opacity = rt_double(spec.get("opacity"))
        opacity = 0.15 if opacity is None else opacity
        base = self.token_color(spec.get("color"), False) if spec.get("color") is not None else None
        color = self.static_color(spec.get("color")) or base or "Color.black"
        radius = self.resolve_number(spec.get("radius"), None)
        x = self.resolve_number(spec.get("x"), None)
        y = self.resolve_number(spec.get("y"), None)
        return (f"ThemeShadow(color: {color}.opacity({num(opacity)}), radius: {num(8 if radius is None else radius)}, "
                f"x: {num(0 if x is None else x)}, y: {num(2 if y is None else y)})")

    def static_color(self, value) -> Optional[str]:
        """Swift expression for a static color value used by a node (tokens by name)."""
        s = rt_string(value)
        if s is None:
            return None
        s = s.strip()
        if s.startswith("$"):
            name = s[1:]
            if name in self.color_names and self.token_defined(name):
                return f"Color.{self.color_names[name]}"
            return None
        if s.startswith("#"):
            return hex_color(s)
        return NAMED_COLORS.get(s)

    def token_defined(self, name: str) -> bool:
        light = self.token_color(self.colors[name], False) if name in self.colors else None
        dark_src = self.colors_dark.get(name, self.colors.get(name))
        dark = self.token_color(dark_src, True) if dark_src is not None else None
        return light is not None or dark is not None

    # -- tabs, routes, state ---------------------------------------------------

    def read_tabs(self) -> list:
        tabs = []
        n = Names(SWIFT_KEYWORDS)
        raw = self.app.get("tabs")
        if isinstance(raw, list):
            for i, entry in enumerate(raw):
                t = entry if isinstance(entry, dict) else {}
                screen = t.get("screen") if isinstance(t.get("screen"), str) else (
                    t.get("id") if isinstance(t.get("id"), str) else "")
                tid = t.get("id") if isinstance(t.get("id"), str) else f"tab{i}"
                title = t.get("title") if isinstance(t.get("title"), str) else " ".join(
                    w[:1].upper() + w[1:].lower() for w in screen.split(" "))
                symbol = t.get("symbol") if isinstance(t.get("symbol"), str) else "circle"
                case = n.get(lower_camel(tid, "tab"))
                tabs.append({"id": tid, "title": title, "symbol": symbol, "screen": screen, "case": case,
                             "path": case + "Path"})
        return tabs

    @property
    def root_screen(self) -> str:
        root = self.app.get("root")
        if isinstance(root, str):
            return root
        return sorted(self.screens)[0] if self.screens else ""

    def all_nodes(self, sid: str):
        """Every node of a screen (body, toolbar, repeat templates), with effective props."""
        js = self.screens[sid]
        stack = []
        body = screen_body(sid, js)
        if body is not None:
            stack.append(body)
        for t in js.get("toolbar") or []:
            if isinstance(t, dict):
                stack.append(t)
        while stack:
            node = stack.pop()
            yield node
            for c in node.get("children") or []:
                if isinstance(c, dict):
                    stack.append(c)
            if isinstance(node.get("template"), dict):
                stack.append(node["template"])

    def effective(self, node: dict) -> dict:
        raw = node.get("style")
        props = {k: v for k, v in node.items() if k not in ("type", "id", "children", "template")}
        if isinstance(raw, str):
            bundle = self.styles.get(raw[1:] if raw.startswith("$") else raw)
            if isinstance(bundle, dict):
                merged = {k: v for k, v in bundle.items() if k != "style"}
                merged.update({k: v for k, v in props.items() if k != "style"})
                return merged
        return props

    def collect_routes(self):
        """One Route case per screen that is navigated to or presented, with the param
        keys any call site passes (all optional, so each call passes what it has)."""
        params = {}
        for sid in self.screens:
            for node in self.all_nodes(sid):
                p = self.effective(node)
                for key in ("action", "onTap"):
                    for a in flat_actions(p.get(key)):
                        target = a.get("navigate") if a.get("navigate") is not None else a.get("sheet")
                        target = rt_string(target)
                        if target and "{{" not in target and target in self.screens:
                            keys = params.setdefault(target, set())
                            if isinstance(a.get("params"), dict):
                                keys.update(str(k) for k in a["params"])
        n = Names(SWIFT_KEYWORDS | {"id"})
        self.routes = {}
        for sid in sorted(params):
            pn = Names(SWIFT_KEYWORDS | SCREEN_RESERVED)
            self.routes[sid] = {"case": n.get(lower_camel(sid, "screen")),
                                "params": [(k, pn.get(lower_camel(k, "param"))) for k in sorted(params[sid])]}

    def build_state(self):
        """AppState properties: app.json state, plus every key the design binds, toggles,
        sets or tests in visibleIf (typed by first use)."""
        self.state = {}  # key -> {"ident", "type", "initial"}
        n = Names(SWIFT_KEYWORDS | {"objectWillChange", "isVisible", "truthy", "string"})
        initial = self.app.get("state") if isinstance(self.app.get("state"), dict) else {}

        def add(key: str, typ: str, value=None):
            if key in self.state or not key:
                return
            self.state[key] = {"ident": n.get(lower_camel(key, "value")), "type": typ, "initial": value}

        for key, value in initial.items():
            add(str(key), swift_type_of(value), value)
        pickers = {}
        for sid in self.screens:
            for node in self.all_nodes(sid):
                p = self.effective(node)
                t = node.get("type")
                bind = rt_string(p.get("bind")) or (node.get("id") if isinstance(node.get("id"), str) else None)
                if t == "toggle" and bind:
                    add(bind, "Bool", False)
                elif t in ("textfield", "picker") and bind:
                    add(bind, "String", "")
                    if t == "picker" and isinstance(p.get("options"), list):
                        pickers.setdefault(bind, [rt_string(o) for o in p["options"] if rt_string(o) is not None])
                for key in ("action", "onTap"):
                    for a in flat_actions(p.get(key)):
                        if rt_string(a.get("toggle")):
                            add(rt_string(a["toggle"]), "Bool", False)
                        elif isinstance(a.get("set"), dict) and not any(
                                a.get(v) is not None for v in ("navigate", "sheet", "dismiss", "tab", "toggle")):
                            for k, v in a["set"].items():
                                add(str(k), swift_type_of(v) if swift_type_of(v) != "Other" else "String",
                                    default_for(swift_type_of(v)))
                cond = rt_string(p.get("visibleIf"))
                if cond and "{{" not in cond:
                    for op in ("!=", "=="):
                        if op in cond:
                            break
                    else:
                        key = cond.strip().lstrip("!")
                        if key.lower() not in ("true", "yes", "1", "false", "no", "0", ""):
                            add(key, "Bool", False)
        for key, info in self.state.items():
            if info["initial"] is None or swift_type_of(info["initial"]) != info["type"]:
                info["initial"] = default_for(info["type"])
            options = pickers.get(key)
            if options and info["type"] == "String" and info["initial"] not in options:
                info["initial"] = options[0]  # the runtime shows the first option for unknown values

    # -- output ----------------------------------------------------------------

    def generate(self) -> dict:
        files = {
            "project.yml": self.project_yml(),
            "run.sh": RUN_SH.replace("__APP_NAME__", self.app_type).replace("__BUNDLE_ID__", self.bundle_id),
            "Sources/Theme.swift": self.theme_swift(),
            "Sources/Support.swift": SUPPORT_SWIFT,
            "Sources/AppState.swift": self.state_swift(),
            "Sources/Router.swift": self.router_swift(),
            "Sources/RootView.swift": self.root_swift(),
            f"Sources/{self.app_type}App.swift": self.app_swift(),
        }
        for sid in sorted(self.screens):
            files[f"Sources/Screens/{self.screen_types[sid]}.swift"] = ScreenGen(self, sid).swift()
        files["README.md"] = self.readme()
        return files

    def project_yml(self) -> str:
        return f"""name: {self.app_type}
options:
  bundleIdPrefix: {self.bundle_id.rsplit('.', 1)[0] if '.' in self.bundle_id else self.bundle_id}
  deploymentTarget:
    iOS: "26.0"
  createIntermediateGroups: true
settings:
  base:
    SWIFT_VERSION: "5.0"
    SWIFT_STRICT_CONCURRENCY: minimal
    MARKETING_VERSION: "1.0"
    CURRENT_PROJECT_VERSION: "1"
targets:
  {self.app_type}:
    type: application
    platform: iOS
    sources:
      - path: Sources
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: {self.bundle_id}
        INFOPLIST_KEY_CFBundleDisplayName: {json.dumps(self.app.get("name") if isinstance(self.app.get("name"), str) else self.name)}
        GENERATE_INFOPLIST_FILE: YES
        INFOPLIST_KEY_UILaunchScreen_Generation: YES
        TARGETED_DEVICE_FAMILY: "1,2"
"""

    def theme_swift(self) -> str:
        L = [HEADER.format(file="Theme.swift", source="design/tokens.json"), "import SwiftUI", "import UIKit", ""]
        L.append("extension Color {")
        for key, ident in self.color_names.items():
            light = self.token_color(self.colors[key], False) if key in self.colors else None
            dark_src = self.colors_dark.get(key, self.colors.get(key))
            dark = self.token_color(dark_src, True) if dark_src is not None else None
            if light is None and dark is None:
                L.append(f"    {self.todo('Theme.swift', f'color token {key!r} has no usable value')}")
                continue
            if dark is None or dark == light:
                L.append(f"    static let {ident} = {light}")
            else:
                L.append(f"    static let {ident} = Color(light: {light or 'Color.clear'}, dark: {dark})")
        L.append("}")
        L.append("")
        L.append("enum Theme {")
        fonts = []
        for key, ident in self.font_names.items():
            expr = self.font_expr(*self.font_spec({"font": "$" + key}))
            if expr:
                fonts.append(f"        static let {ident} = {expr}")
        spacing = self.number_lines(self.spacing, self.spacing_names)
        radius = self.number_lines(self.radius, self.radius_names)
        shadows = []
        for key, ident in self.shadow_names.items():
            spec = self.shadows[key]
            if isinstance(spec, dict):
                shadows.append(f"        static let {ident} = {self.shadow_expr(spec)}")
        for title, lines in (("Fonts", fonts), ("Spacing", spacing), ("Radius", radius), ("Shadows", shadows)):
            L.append(f"    enum {title} {{")
            L.extend(lines)
            L.append("    }")
            L.append("")
        L.append("    /// Resolves a color written as text (\"$token\", \"#RRGGBB\" or a system color")
        L.append("    /// name) at runtime, for colors that come from sample data.")
        L.append("    static func color(_ value: String) -> Color? {")
        L.append("        let s = value.trimmingCharacters(in: .whitespaces)")
        L.append("        if s.hasPrefix(\"$\") {")
        L.append("            switch String(s.dropFirst()) {")
        for key, ident in self.color_names.items():
            if self.token_defined(key):
                L.append(f"            case {swift_str(key)}: return Color.{ident}")
        L.append("            default: return nil")
        L.append("            }")
        L.append("        }")
        L.append("        if s.hasPrefix(\"#\") { return Color(hexString: s) }")
        L.append("        switch s {")
        by_expr = {}
        for name, expr in NAMED_COLORS.items():
            by_expr.setdefault(expr, []).append(name)
        for expr, names in by_expr.items():
            L.append(f"        case {', '.join(swift_str(n) for n in names)}: return {expr}")
        L.append("        default: return nil")
        L.append("        }")
        L.append("    }")
        L.append("}")
        L.append("")
        L.append(THEME_SUPPORT)
        return "\n".join(L)

    def number_lines(self, group: dict, names: dict) -> list:
        out = []
        for key, ident in names.items():
            v = self.resolve_number(group[key], None)
            if v is not None:
                out.append(f"        static let {ident}: CGFloat = {num(v)}")
        return out

    def state_swift(self) -> str:
        L = [HEADER.format(file="AppState.swift", source="design/app.json \"state\" and the keys the design binds"),
             "import SwiftUI", "", "/// The prototype's demo state: toggles, text fields, pickers and `set` actions.",
             "final class AppState: ObservableObject {"]
        for key, info in self.state.items():
            if info["type"] == "Other":
                L.append(f"    {self.todo('AppState.swift', f'state {key!r} holds a list/object; not generated')}")
                continue
            L.append(f"    @Published var {info['ident']}: {info['type']} = {literal(info['initial'], info['type'])}")
        L.append("")
        L.append("    /// Text form of a state value (for conditions built from sample data).")
        L.append("    func string(forKey key: String) -> String? {")
        L.append("        switch key {")
        for key, info in self.state.items():
            if info["type"] == "String":
                L.append(f"        case {swift_str(key)}: return {info['ident']}")
            elif info["type"] in ("Bool", "Int", "Double"):
                L.append(f"        case {swift_str(key)}: return String({info['ident']})")
        L.append("        default: return nil")
        L.append("        }")
        L.append("    }")
        L.append("")
        L.append(STATE_SUPPORT)
        L.append("}")
        return "\n".join(L) + "\n"

    def router_swift(self) -> str:
        L = [HEADER.format(file="Router.swift", source="design/app.json tabs and the design's actions"),
             "import SwiftUI", ""]
        if self.tabs:
            L.append("enum AppTab: String, Hashable {")
            for t in self.tabs:
                L.append(f"    case {t['case']} = {swift_str(t['id'])}")
            L.append("}")
            L.append("")
        L.append("/// Every screen that is pushed or presented, with the params callers pass.")
        L.append("enum Route: Hashable, Identifiable {")
        for sid, r in self.routes.items():
            if r["params"]:
                args = ", ".join(f"{ident}: String? = nil" for _, ident in r["params"])
                L.append(f"    case {r['case']}({args})")
            else:
                L.append(f"    case {r['case']}")
        L.append("")
        L.append("    var id: Self { self }")
        L.append("")
        L.append("    @ViewBuilder var destination: some View {")
        if self.routes:
            L.append("        switch self {")
            for sid, r in self.routes.items():
                idents = [ident for _, ident in r["params"]]
                if idents:
                    args = ", ".join(f"{i}: {i}" for i in idents)
                    L.append(f"        case let .{r['case']}({', '.join(idents)}):")
                    L.append(f"            {self.screen_types[sid]}({args})")
                else:
                    L.append(f"        case .{r['case']}:")
                    L.append(f"            {self.screen_types[sid]}()")
            L.append("        }")
        else:
            L.append("        EmptyView()")
        L.append("    }")
        L.append("}")
        L.append("")
        L.append("/// Navigation state: the selected tab, one stack per tab, the sheet and alerts.")
        L.append("final class Router: ObservableObject {")
        if self.tabs:
            L.append(f"    @Published var selectedTab: AppTab = .{self.tabs[0]['case']}")
            for t in self.tabs:
                L.append(f"    @Published var {t['path']}: [Route] = []")
        else:
            L.append("    @Published var rootPath: [Route] = []")
        L.append("    @Published var sheet: Route?")
        L.append("    @Published var sheetPath: [Route] = []")
        L.append("    @Published var alertTitle = \"\"")
        L.append("    @Published var alertMessage = \"\"")
        L.append("    @Published var alertShown = false")
        L.append("")
        if self.tabs:
            L.append("    init() {")
            L.append("        #if DEBUG")
            L.append("        // Launch with `-initialTab <id>` to start on another tab.")
            L.append("        if let id = UserDefaults.standard.string(forKey: \"initialTab\"), let tab = AppTab(rawValue: id) {")
            L.append("            selectedTab = tab")
            L.append("        }")
            L.append("        #endif")
            L.append("    }")
            L.append("")
        L.append("    func navigate(_ route: Route) {")
        L.append("        if sheet != nil {")
        L.append("            sheetPath.append(route)")
        if self.tabs:
            L.append("            return")
            L.append("        }")
            L.append("        switch selectedTab {")
            for t in self.tabs:
                L.append(f"        case .{t['case']}: {t['path']}.append(route)")
            L.append("        }")
        else:
            L.append("        } else {")
            L.append("            rootPath.append(route)")
            L.append("        }")
        L.append("    }")
        L.append("")
        L.append("    func present(_ route: Route) {")
        L.append("        sheetPath = []")
        L.append("        sheet = route")
        L.append("    }")
        L.append("")
        L.append("    func dismiss() {")
        L.append("        if sheet != nil {")
        L.append("            if sheetPath.isEmpty { sheet = nil } else { sheetPath.removeLast() }")
        if self.tabs:
            L.append("            return")
            L.append("        }")
            L.append("        switch selectedTab {")
            for t in self.tabs:
                L.append(f"        case .{t['case']}: if !{t['path']}.isEmpty {{ {t['path']}.removeLast() }}")
            L.append("        }")
        else:
            L.append("        } else if !rootPath.isEmpty {")
            L.append("            rootPath.removeLast()")
            L.append("        }")
        L.append("    }")
        L.append("")
        L.append(ROUTER_SUPPORT)
        L.append("}")
        return "\n".join(L) + "\n"

    def root_swift(self) -> str:
        L = [HEADER.format(file="RootView.swift", source="design/app.json"), "import SwiftUI", "",
             "struct RootView: View {",
             "    @EnvironmentObject var router: Router",
             "    @EnvironmentObject var state: AppState", "",
             "    var body: some View {"]
        if self.tabs:
            L.append("        TabView(selection: $router.selectedTab) {")
            for t in self.tabs:
                L.append(f"            NavigationStack(path: $router.{t['path']}) {{")
                L.append(f"                {self.screen_call(t['screen'])}")
                L.append("                    .navigationDestination(for: Route.self) { $0.destination }")
                L.append("            }")
                L.append(f"            .tabItem {{ Label({swift_str(t['title'])}, systemImage: {swift_str(t['symbol'])}) }}")
                L.append(f"            .tag(AppTab.{t['case']})")
            L.append("        }")
        else:
            L.append("        NavigationStack(path: $router.rootPath) {")
            L.append(f"            {self.screen_call(self.root_screen)}")
            L.append("                .navigationDestination(for: Route.self) { $0.destination }")
            L.append("        }")
        tint = self.static_color(self.app.get("tint"))
        if tint:
            L.append(f"        .tint({tint})")
        scheme = self.app.get("colorScheme")
        if scheme in ("light", "dark"):
            L.append(f"        .preferredColorScheme(.{scheme})")
        L.extend([
            "        .sheet(item: $router.sheet) { route in",
            "            NavigationStack(path: $router.sheetPath) {",
            "                route.destination",
            "                    .navigationDestination(for: Route.self) { $0.destination }",
            "            }",
            "            // A presented sheet blocks alerts on the root view, so it has its own.",
            "            .alert(router.alertTitle, isPresented: router.alertBinding(inSheet: true)) {",
            "                Button(\"OK\", role: .cancel) {}",
            "            } message: {",
            "                Text(router.alertMessage)",
            "            }",
            "            .environmentObject(router)",
            "            .environmentObject(state)",
            "        }",
            "        .alert(router.alertTitle, isPresented: router.alertBinding(inSheet: false)) {",
            "            Button(\"OK\", role: .cancel) {}",
            "        } message: {",
            "            Text(router.alertMessage)",
            "        }",
            "    }",
            "}",
        ])
        return "\n".join(L) + "\n"

    def screen_call(self, sid: str) -> str:
        if sid in self.screen_types:
            return f"{self.screen_types[sid]}()"
        return f'EjectPlaceholder("missing screen {sid}")'

    def app_swift(self) -> str:
        return "\n".join([
            HEADER.format(file=f"{self.app_type}App.swift", source="design/app.json"),
            "import SwiftUI", "",
            "@main",
            f"struct {self.app_type}App: App {{",
            "    @StateObject private var state = AppState()",
            "    @StateObject private var router = Router()", "",
            "    var body: some Scene {",
            "        WindowGroup {",
            "            RootView()",
            "                .environmentObject(state)",
            "                .environmentObject(router)",
            "        }",
            "    }",
            "}", ""])

    def readme(self) -> str:
        screens = "\n".join(f"- `Sources/Screens/{self.screen_types[s]}.swift` ← `design/screens/{s}.json`"
                            for s in sorted(self.screens))
        todos = "\n".join(f"- {t}" for t in self.todos) or "- none"
        return f"""# {self.name} (SwiftUI)

Generated by `./ui eject` from the live-UI prototype's `design/` folder. This is plain
SwiftUI with no JSON runtime: edit it like any app. Re-running `./ui eject` refuses to
overwrite files you changed here unless you pass `--force`.

## Build and run
```bash
./run.sh                      # xcodegen + build + launch in the Simulator
./run.sh -initialTab explore  # (Debug) start on another tab
```
Or `xcodegen generate && open {self.app_type}.xcodeproj`.

## Where things are
- `Sources/Theme.swift` — design tokens: `Color.<token>`, `Theme.Fonts`, `Theme.Spacing`, `Theme.Radius`, `Theme.Shadows`
- `Sources/AppState.swift` — demo state (toggles, text fields, pickers)
- `Sources/Router.swift` — tabs, `Route` (pushed and presented screens) and alerts
- `Sources/RootView.swift` — tab bar / navigation stacks, sheet, alerts
{screens}
- `Sources/Support.swift` — `RemoteImage` (URL images) and `EjectPlaceholder`

Sample data from the design is inlined in each screen; replace it with your model layer.

## Left to do
{todos}
"""


# --------------------------------------------------------------------------
# One screen
# --------------------------------------------------------------------------


class ScreenGen:
    def __init__(self, ej: Ejector, sid: str):
        self.ej = ej
        self.sid = sid
        self.js = ej.screens[sid]
        self.type = ej.screen_types[sid]
        self.file = f"{self.type}.swift"
        self.data = self.js.get("data") if isinstance(self.js.get("data"), dict) else {}
        route = ej.routes.get(sid)
        self.params = {k: ident for k, ident in (route["params"] if route else [])}
        self.names = Names(SWIFT_KEYWORDS | SCREEN_RESERVED | set(self.params.values()))
        self.members = []      # extra computed properties / functions (lines)
        self.constants = []    # sample data (lines)
        self.structs = []      # item types (lines)
        self.collections = {}  # data path -> (constant name, fields, struct name)
        self.uses_open_url = False

    # -- scope / values ------------------------------------------------------

    def resolve(self, key: str, scope: Scope):
        """("lit", value) or ("expr", swift, type) for a {{key}}, in the runtime's
        order: index/number, repeat item, params, screen data, app state."""
        if key == "index" and scope.index:
            return ("expr", "index", "Int")
        if key == "number" and scope.index:
            return ("expr", "index + 1", "Int")
        head, _, tail = key.partition(".")
        if tail and head in ("item", "params", "data", "state"):
            return self.resolve_in(head, tail, scope) or ("lit", "")
        for where in ("item", "params", "data", "state"):
            r = self.resolve_in(where, key, scope)
            if r is not None:
                return r
        return ("lit", "")

    def resolve_in(self, where: str, path: str, scope: Scope):
        parts = path.split(".")
        if where == "item":
            if scope.item is None or parts[0] not in scope.fields:
                return None
            ident, typ = scope.fields[parts[0]]
            if len(parts) > 1:
                return ("lit", "")
            return ("expr", f"{scope.item}.{ident}" if ident else scope.item, typ)
        if where == "params":
            if len(parts) != 1 or path not in self.params:
                return None
            fallback = self.resolve_in("data", path, scope) or self.resolve_in("state", path, scope)
            return ("expr", f"{self.params[path]} ?? {self.as_string(fallback)}", "String")
        if where == "data":
            v = lookup(self.data, path)
            if v is None:
                return None
            return ("lit", rt_string(v) if not isinstance(v, (list, dict)) else "")
        if where == "state":
            info = self.ej.state.get(path)
            if info is None or info["type"] == "Other":
                return None
            return ("expr", f"state.{info['ident']}", info["type"])
        return None

    @staticmethod
    def as_string(r) -> str:
        if r is None:
            return '""'
        if r[0] == "lit":
            return swift_str(rt_string(r[1]) or "")
        return r[1] if r[2] == "String" else f'"\\({r[1]})"'

    def text(self, raw, scope: Scope) -> Optional[Text]:
        s = rt_string(raw)
        if s is None:
            return None
        parts = split_template(s)
        out, static, safe, string_only = [], True, True, True
        resolved = []
        for kind, value in parts:
            if kind == "lit":
                resolved.append(("lit", value))
                continue
            r = self.resolve(value, scope)
            if r[0] == "lit":
                resolved.append(("lit", rt_string(r[1]) or ""))
            else:
                static = False
                resolved.append(r)
        if static:
            value = "".join(v for _, v in resolved)
            return Text(value, swift_str(value), not (set(value) & MARKDOWN_CHARS), True)
        exprs = [r for r in resolved if r[0] == "expr"]
        lits = "".join(v for k, v in [(r[0], r[1]) for r in resolved] if k == "lit")
        safe = not (set(lits) & MARKDOWN_CHARS)
        string_only = all(r[2] == "String" for r in exprs)
        if len(resolved) == 1 and exprs and exprs[0][2] == "String":
            return Text(None, exprs[0][1], True, True)
        for r in resolved:
            if r[0] == "lit":
                out.append(swift_str(r[1])[1:-1])
            else:
                out.append(f"\\({r[1]})")
        return Text(None, '"' + "".join(out) + '"', safe, string_only)

    def number(self, raw, group: Optional[dict] = None, allow_inf: bool = False) -> Optional[Num]:
        """A CGFloat expression: `Theme.Spacing.md` for tokens (looked up in `group`, then
        spacing, then radius — like LiveTheme.number), else a literal."""
        s = rt_string(raw)
        if s is None or "{{" in s:
            return None
        t = s.strip()
        ej = self.ej
        if t.startswith("$"):
            name = t[1:]
            for g in ([group] if group is not None else []) + [ej.spacing, ej.radius]:
                if name not in g:
                    continue
                v = ej.resolve_number(g[name], None, allow_inf)
                if v is None:
                    return None
                if math.isinf(v):
                    return Num(".infinity", v)
                names, title = (ej.spacing_names, "Spacing") if g is ej.spacing else (ej.radius_names, "Radius")
                return Num(f"Theme.{title}.{names[name]}", v)
            return None
        v = ej.resolve_number(t, None, allow_inf)
        if v is None:
            return None
        return Num(".infinity" if math.isinf(v) else num(v), v)

    def color(self, raw, scope: Scope) -> Optional[tuple]:
        """(expr, optional): optional is True for colors resolved at runtime from sample data."""
        s = rt_string(raw)
        if s is None:
            return None
        if "{{" in s:
            t = self.text(raw, scope)
            if t.static is None:
                return (f"Theme.color({t.swift})", True)
            s = t.static
        expr = self.ej.static_color(s)
        return (expr, False) if expr else None

    def fill(self, raw, scope: Scope, fallback: str) -> Optional[str]:
        """A ShapeStyle expression: a color, or a LinearGradient for {"gradient": [...]}."""
        if isinstance(raw, dict) and isinstance(raw.get("gradient"), list):
            colors = [c for c in (self.color(x, scope) for x in raw["gradient"]) if c]
            if not colors:
                return None
            exprs = [f"{e} ?? Color.clear" if opt else e for e, opt in colors]
            if len(colors) < 2:
                return exprs[0] if not colors[0][1] else f"({exprs[0]})"
            direction = rt_string(raw.get("direction")) or "down"
            start, end = {"right": (".leading", ".trailing"), "diagonal": (".topLeading", ".bottomTrailing"),
                          "up": (".bottom", ".top")}.get(direction, (".top", ".bottom"))
            return f"LinearGradient(colors: [{', '.join(exprs)}], startPoint: {start}, endPoint: {end})"
        c = self.color(raw, scope)
        if c is None:
            return None
        return f"{c[0]} ?? {fallback}" if c[1] else c[0]

    def condition(self, props: dict, scope: Scope):
        """visibleIf -> None (always), True/False (decided now) or a Swift condition."""
        if "visibleIf" not in props:
            return None
        raw = props.get("visibleIf")
        s = rt_string(raw)
        if s is None:
            return None
        if "{{" in s:
            t = self.text(raw, scope)
            if t.static is None:
                return f"state.isVisible({t.swift})"
            s = t.static
        c = s.strip()
        for op in ("!=", "=="):
            if op in c:
                lhs, rhs = (x.strip() for x in c.split(op, 1))
                info = self.ej.state.get(lhs)
                if info is None or info["type"] == "Other":
                    return (lhs == rhs) if op == "==" else (lhs != rhs)
                return compare(f"state.{info['ident']}", info["type"], op, rhs)
        negate = c.startswith("!")
        key = c[1:] if negate else c
        low = key.lower()
        if low in ("true", "yes", "1"):
            return not negate
        if low in ("false", "no", "0", ""):
            return negate
        info = self.ej.state.get(key)
        if info is None or info["type"] == "Other":
            return negate
        expr = truthy(f"state.{info['ident']}", info["type"])
        return f"!{expr}" if negate else expr

    # -- statements and views ------------------------------------------------

    def stmt(self, node, scope: Scope, ind: int) -> list:
        """Lines for a node inside a ViewBuilder (if visibleIf, ForEach for repeat)."""
        if not isinstance(node, dict):
            return []
        p = self.ej.effective(node)
        cond = self.condition(p, scope)
        if cond is False:
            return []
        if node.get("type") == "repeat":
            inner = self.repeat_loop(node, p, scope, ind + (1 if isinstance(cond, str) else 0))
        else:
            inner = self.view(node, scope, ind + (1 if isinstance(cond, str) else 0))
        if not isinstance(cond, str):
            return inner
        return [IND * ind + f"if {cond} {{"] + inner + [IND * ind + "}"]

    def view(self, node: dict, scope: Scope, ind: int, extract: bool = True) -> list:
        """Lines for one view expression: content plus modifiers, in the runtime's order."""
        if extract and scope.item is None and self.worth_extracting(node):
            name = self.member_name(node)
            body = self.view(node, scope, 2, extract=False)
            self.members.append([f"{IND}private var {name}: some View {{"] + body + [f"{IND}}}"])
            return [IND * ind + name]
        p = self.ej.effective(node)
        t = node.get("type")
        if t == "button":
            return self.button(node, p, scope, ind)
        head = self.content(node, p, scope, ind)
        mods = self.text_mods(t, p, scope) + self.layout_mods(t, p, scope) + self.decoration_mods(t, p, scope)
        mods += self.behavior_mods(p, scope, allow_tap=True)
        return self.attach(head, mods, ind)

    @staticmethod
    def attach(head: list, mods: list, ind: int) -> list:
        """Appends modifiers Xcode-style: indented under a one-line view, aligned with
        the closing brace of a multi-line one. Modifier lines already at the end of
        `head` (added by content()) are re-indented the same way."""
        pad = IND * ind
        i = len(head)
        while i > 1 and head[i - 1].startswith(pad + "."):
            i -= 1
        core, trailing = head[:i], [line[len(pad):] for line in head[i:]]
        level = ind + (1 if len(core) == 1 else 0)
        out = list(core)
        for m in trailing + mods:
            out.extend(indent_block(m, level))
        return out

    def worth_extracting(self, node: dict) -> bool:
        t = node.get("type")
        if t not in ("vstack", "hstack", "zstack", "scroll", "grid", "list") and not (
                t == "button" and node.get("children")):
            return False
        count, stack = 0, [node]
        while stack:
            n = stack.pop()
            count += 1
            stack.extend(c for c in (n.get("children") or []) if isinstance(c, dict))
            if isinstance(n.get("template"), dict):
                stack.append(n["template"])
        return count >= 4

    def member_name(self, node: dict) -> str:
        nid = node.get("id") if isinstance(node.get("id"), str) else ""
        if nid.startswith(self.sid + "."):
            nid = nid[len(self.sid) + 1:]
        base = lower_camel(nid, node.get("type", "part"))
        if base in SCREEN_RESERVED:
            base += "View"
        return self.names.get(base)

    def children(self, node: dict, scope: Scope, ind: int) -> list:
        out = []
        for c in node.get("children") or []:
            out.extend(self.stmt(c, scope, ind))
        return out

    def content(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        t = node.get("type")
        pad = IND * ind
        if t == "vstack":
            args = self.stack_args(H_ALIGN.get(rt_string(p.get("align")) or ""), p)
            return [pad + f"VStack{args} {{"] + self.children(node, scope, ind + 1) + [pad + "}"]
        if t == "hstack":
            args = self.stack_args(V_ALIGN.get(rt_string(p.get("align")) or ""), p)
            return [pad + f"HStack{args} {{"] + self.children(node, scope, ind + 1) + [pad + "}"]
        if t == "zstack":
            align = ALIGN_9.get(rt_string(p.get("align")) or "", ".center")
            args = "" if align == ".center" else f"(alignment: {align})"
            return [pad + f"ZStack{args} {{"] + self.children(node, scope, ind + 1) + [pad + "}"]
        if t == "scroll":
            return self.scroll(node, p, scope, ind)
        if t == "grid":
            count = min(max(1, rt_int(rt_string(p.get("columns"))) or 2), 12)
            sp = self.number(p.get("spacing"), self.ej.spacing)
            spacing = sp.expr if sp else "8"
            return ([pad + f"LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: {spacing}), "
                     f"count: {count}), spacing: {spacing}) {{"]
                    + self.children(node, scope, ind + 1) + [pad + "}"])
        if t == "list":
            return self.list_view(node, p, scope, ind)
        if t == "section":
            return self.loose_section(node, p, scope, ind)
        if t == "repeat":
            sp = self.number(p.get("spacing"), self.ej.spacing)
            args = f"(spacing: {sp.expr})" if sp else ""
            return [pad + f"VStack{args} {{"] + self.repeat_loop(node, p, scope, ind + 1) + [pad + "}"]
        if t == "text":
            tv = self.text(p.get("text"), scope) or Text("", '""', True, True)
            return [pad + tv.text_view()]
        if t == "label":
            title = self.text(p.get("text"), scope) or Text("", '""', True, True)
            symbol = self.text(p.get("symbol"), scope)
            if symbol is not None:
                return [pad + f"Label({title.title()}, systemImage: {symbol.swift})"]
            return [pad + title.text_view()]
        if t == "image":
            return self.image(node, p, scope, ind)
        if t == "toggle":
            key = rt_string(p.get("bind")) or node.get("id", "")
            title = self.text(p.get("text"), scope) or Text("", '""', True, True)
            return [pad + f"Toggle({title.title()}, isOn: {self.binding(key, 'Bool')})"]
        if t == "textfield":
            key = rt_string(p.get("bind")) or node.get("id", "")
            placeholder = self.text(p.get("placeholder"), scope) or Text("", '""', True, True)
            kind = "SecureField" if rt_bool(rt_string(p.get("secure"))) is True else "TextField"
            lines = [pad + f"{kind}({placeholder.title()}, text: {self.binding(key, 'String')})"]
            if rt_string(p.get("fieldStyle")) != "plain":
                lines.append(pad + ".textFieldStyle(.roundedBorder)")
            lines += [pad + m for m in KEYBOARDS.get(rt_string(p.get("keyboard")) or "", [])]
            return lines
        if t == "picker":
            key = rt_string(p.get("bind")) or node.get("id", "")
            title = self.text(p.get("text"), scope) or Text("", '""', True, True)
            options = [self.text(o, scope) for o in (p.get("options") if isinstance(p.get("options"), list) else [])]
            opts = ", ".join(o.swift for o in options if o is not None)
            style = rt_string(p.get("pickerStyle"))
            style = style if style in ("menu", "wheel", "inline") else "segmented"
            return [pad + f"Picker({title.title()}, selection: {self.binding(key, 'String')}) {{",
                    pad + IND + f"ForEach({f'[{opts}]' if opts else '[String]()'}, id: \\.self) {{ option in",
                    pad + IND * 2 + "Text(option).tag(option)",
                    pad + IND + "}",
                    pad + "}",
                    pad + f".pickerStyle(.{style})"]
        if t == "shape":
            fill = self.fill(p.get("fill"), scope, "Color.clear") or "Color.accentColor"
            kind = rt_string(p.get("shape")) or "rect"
            if kind == "circle":
                return [pad + f"Circle().fill({fill})"]
            if kind == "capsule":
                return [pad + f"Capsule().fill({fill})"]
            if kind == "rounded":
                r = self.number(p.get("radius"), self.ej.radius)
                return [pad + f"RoundedRectangle(cornerRadius: {r.expr if r else '12'}, style: .continuous).fill({fill})"]
            return [pad + f"Rectangle().fill({fill})"]
        if t == "progress":
            raw = rt_string(p.get("value"))
            if raw is not None and "{{" in raw:
                tv = self.text(p.get("value"), scope)
                if tv.static is None:
                    return [pad + f"ProgressView(value: min(max(Double({tv.swift}) ?? 0, 0), 1))"]
                raw = tv.static
            value = rt_double(raw)
            if value is not None and math.isfinite(value):
                return [pad + f"ProgressView(value: {num(min(max(value, 0), 1))})"]
            return [pad + "ProgressView()"]
        if t == "spacer":
            m = self.number(p.get("minLength"))
            return [pad + (f"Spacer(minLength: {m.expr})" if m else "Spacer()")]
        if t == "divider":
            return [pad + "Divider()"]
        if t == "custom":
            name = rt_string(p.get("name")) or ""
            shown = {k: v for k, v in p.items() if k not in HANDLED_PROPS or k in ("name",)}
            note = self.ej.todo(self.file, f"custom component {name!r} — port it from the prototype's "
                                           f"Sources/Components (props: {json.dumps(shown, ensure_ascii=False)[:120]})")
            return [pad + note, pad + f"EjectPlaceholder({swift_str(name or 'custom')})"]
        note = self.ej.todo(self.file, f"unknown node type {t!r} ({node.get('id')})")
        return [pad + note, pad + f"EjectPlaceholder({swift_str('unknown type ' + str(t))})"]

    def stack_args(self, align: Optional[str], p: dict) -> str:
        args = []
        if align:
            args.append(f"alignment: {align}")
        sp = self.number(p.get("spacing"), self.ej.spacing)
        if sp:
            args.append(f"spacing: {sp.expr}")
        return f"({', '.join(args)})" if args else ""

    def binding(self, key: str, want: str) -> str:
        info = self.ej.state.get(key)
        if info is None or info["type"] != want:
            note = self.ej.todo(self.file, f"{key!r} is bound as {want} but the state holds "
                                           f"{info['type'] if info else 'nothing'}")
            return f".constant({literal(default_for(want), want)}) {note.replace('// ', '/* ')} */"
        return f"$state.{info['ident']}"

    def scroll(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        padding = self.insets(p.get("padding"))
        if rt_string(p.get("axis")) == "horizontal":
            args = self.stack_args(V_ALIGN.get(rt_string(p.get("align")) or ""), p)
            lines = [pad + "ScrollView(.horizontal, showsIndicators: false) {", pad + IND + f"HStack{args} {{"]
            lines += self.children(node, scope, ind + 2) + [pad + IND + "}"]
            if padding:
                lines.append(pad + IND + padding)
            return lines + [pad + "}"]
        halign = rt_string(p.get("align")) or ""
        args = self.stack_args(H_ALIGN.get(halign), p)
        frame_align = {"leading": ".topLeading", "trailing": ".topTrailing"}.get(halign, ".top")
        lines = [pad + "ScrollView(.vertical, showsIndicators: false) {", pad + IND + f"VStack{args} {{"]
        lines += self.children(node, scope, ind + 2) + [pad + IND + "}"]
        if padding:
            lines.append(pad + IND + padding)
        lines.append(pad + IND + f".frame(maxWidth: .infinity, alignment: {frame_align})")
        return lines + [pad + "}"]

    def list_view(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        lines = [pad + "List {"]
        for c in node.get("children") or []:
            if isinstance(c, dict) and c.get("type") == "section":
                lines.extend(self.list_section(c, scope, ind + 1))
            else:
                lines.extend(self.stmt(c, scope, ind + 1))
        lines.append(pad + "}")
        style = rt_string(p.get("listStyle")) or "insetGrouped"
        style = style if style in ("plain", "grouped", "inset", "insetGrouped") else "insetGrouped"
        lines.append(pad + f".listStyle(.{style})")
        if p.get("background") is not None:
            lines.append(pad + ".scrollContentBackground(.hidden)")
        return lines

    def list_section(self, node: dict, scope: Scope, ind: int) -> list:
        """A Section inside a List: rows plus header/footer (its own modifiers are ignored,
        like the runtime)."""
        p = self.ej.effective(node)
        cond = self.condition(p, scope)
        if cond is False:
            return []
        inner_ind = ind + (1 if isinstance(cond, str) else 0)
        pad = IND * inner_ind
        header = self.text(p.get("header"), scope)
        footer = self.text(p.get("footer"), scope)
        lines = [pad + "Section {"] + self.children(node, scope, inner_ind + 1)
        if header is not None:
            lines += [pad + "} header: {", pad + IND + header.text_view()]
        if footer is not None:
            lines += [pad + "} footer: {", pad + IND + footer.text_view()]
        lines.append(pad + "}")
        if isinstance(cond, str):
            return [IND * ind + f"if {cond} {{"] + lines + [IND * ind + "}"]
        return lines

    def loose_section(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        header = self.text(p.get("header"), scope)
        footer = self.text(p.get("footer"), scope)
        lines = [pad + "VStack(alignment: .leading, spacing: 8) {"]
        if header is not None:
            lines.append(pad + IND + f"{header.text_view()}.font(.footnote).foregroundStyle(.secondary)"
                                     f".textCase(.uppercase)")
        lines += self.children(node, scope, ind + 1)
        if footer is not None:
            lines.append(pad + IND + f"{footer.text_view()}.font(.footnote).foregroundStyle(.secondary)")
        return lines + [pad + "}"]

    def image(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        symbol = self.text(p.get("symbol"), scope)
        if symbol is not None:
            lines = [pad + f"Image(systemName: {symbol.swift})"]
            mode = rt_string(p.get("symbolMode"))
            if mode in ("hierarchical", "multicolor", "monochrome"):
                lines.append(pad + f".symbolRenderingMode(.{mode})")
            return lines
        mode = ".fit" if rt_string(p.get("fit")) == "fit" else ".fill"
        aspect = self.number(p.get("aspect"))
        asset = self.text(p.get("asset"), scope)
        if asset is not None:
            ratio = aspect.expr if aspect else "nil"
            return [pad + f"Image({asset.swift}).resizable().aspectRatio({ratio}, contentMode: {mode})"]
        url = self.text(p.get("url"), scope)
        if url is not None:
            args = [f"url: {url.swift}"]
            if aspect:
                args.append(f"aspect: {aspect.expr}")
            if mode == ".fit":
                args.append("mode: .fit")
            return [pad + f"RemoteImage({', '.join(args)})"]
        return [pad + 'Image(systemName: "photo")']

    # -- repeat ----------------------------------------------------------------

    def repeat_loop(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        template = node.get("template")
        if not isinstance(template, dict):
            return []
        raw = node.get("items")
        if isinstance(raw, list):
            source = ("list", self.collection(f"repeat:{node.get('id')}", raw, node.get("id") or "items"))
        elif rt_int(raw) is not None and not isinstance(raw, str):
            source = ("count", min(max(0, rt_int(raw)), 500))
        elif isinstance(raw, str):
            path = raw.replace("{{", "").replace("}}", "").strip()
            if path.startswith("$"):
                path = path[1:].strip()
            value = self.value_for_path(path, scope)
            if isinstance(value, list):
                source = ("list", self.collection(path, value, path.split(".")[-1]))
            elif path.isdigit():
                source = ("count", min(int(path), 500))
            elif value == "dynamic":
                return [pad + self.ej.todo(self.file, f"repeat {node.get('id')!r} reads {raw!r} from a repeat item; "
                                                      f"not generated")]
            else:
                return []
        else:
            return []
        if source[0] == "count":
            inner = Scope(self, "index", {"value": (None, "Int")}, True)
            if source[1] == 0:
                return []
            return ([pad + f"ForEach(0..<{source[1]}, id: \\.self) {{ index in"]
                    + self.stmt(template, inner, ind + 1) + [pad + "}"])
        const, fields, struct = source[1]
        if not const:
            return []
        inner = Scope(self, "item", fields, True)
        loop = [pad + f"ForEach(Array({const}.enumerated()), id: \\.offset) {{ index, item in"]
        if scope.item is None:
            fname = self.member_name(template)
            body = self.stmt(template, inner, 2)
            self.members.append([f"{IND}@ViewBuilder", f"{IND}private func {fname}(_ item: {struct}, index: Int) -> some View {{"]
                                + (body or [IND * 2 + "EmptyView()"]) + [f"{IND}}}"])
            return loop + [pad + IND + f"{fname}(item, index: index)", pad + "}"]
        return loop + self.stmt(template, inner, ind + 1) + [pad + "}"]

    def value_for_path(self, path: str, scope: Scope):
        head, _, tail = path.partition(".")
        if head == "item" or (scope.item is not None and path.split(".")[0] in scope.fields):
            return "dynamic"
        if head == "data" and tail:
            return lookup(self.data, tail)
        if head == "state" and tail:
            v = (self.ej.app.get("state") or {}).get(tail) if isinstance(self.ej.app.get("state"), dict) else None
            return v
        if head == "params":
            return None
        v = lookup(self.data, path)
        if v is not None:
            return v
        state = self.ej.app.get("state") if isinstance(self.ej.app.get("state"), dict) else {}
        return state.get(path)

    def collection(self, key: str, items: list, name: str):
        """A struct type and a sample-data constant for the items of a repeat."""
        if key in self.collections:
            return self.collections[key]
        items = items[:500]
        rows = [it if isinstance(it, dict) else {"value": it} for it in items]
        order = []
        for r in rows:
            for k in r:
                if k not in order:
                    order.append(k)
        fn = Names(SWIFT_KEYWORDS)
        fields = {}
        for k in order:
            typ = merged_type([r[k] for r in rows if k in r])
            if typ is None:
                self.ej.todo(self.file, f"field {k!r} of {name!r} holds lists/objects; not generated")
                continue
            fields[k] = (fn.get(lower_camel(k, "field")), typ)
        base = singular(words(name)[-1] if words(name) else "item")
        struct = self.ej.type_names.get(upper_camel(self.sid) + upper_camel(base))
        const = self.names.get(lower_camel(words(name)[-1] if words(name) else "items", "items"))
        if not fields:
            self.collections[key] = (None, {}, struct)
            return self.collections[key]
        self.structs.append([f"struct {struct}: Hashable {{"]
                            + [f"{IND}var {ident}: {typ}" for ident, typ in fields.values()] + ["}"])
        values = []
        for r in rows:
            args = ", ".join(f"{ident}: {literal(r.get(k, default_for(typ)), typ)}" for k, (ident, typ) in fields.items())
            values.append(f"{IND * 2}{struct}({args}),")
        self.constants.append([f"{IND}let {const}: [{struct}] = ["] + values + [f"{IND}]"])
        self.collections[key] = (const, fields, struct)
        return self.collections[key]

    # -- buttons ---------------------------------------------------------------

    def button(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        variant = rt_string(p.get("variant"))
        if variant is None and rt_string(node.get("style")) in BUTTON_VARIANTS:
            variant = rt_string(node.get("style"))
        system = variant in SYSTEM_BUTTON_VARIANTS
        label_props = {k: v for k, v in p.items() if not (system and k in ("background", "radius", "glass"))}
        mods = (self.text_mods("button", label_props, scope) + self.layout_mods("button", label_props, scope)
                + self.decoration_mods("button", label_props, scope))
        label = self.attach(self.button_label(node, p, scope, ind + 1), mods, ind + 1)
        actions = self.actions(p.get("action"), scope, ind + 1)
        role = "(role: .destructive)" if variant == "destructive" else ""
        lines = [pad + f"Button{role} {{"] + actions + [pad + "} label: {"] + label + [pad + "}"]
        out_mods = []
        if variant in BUTTON_STYLES:
            out_mods.append(f".buttonStyle({BUTTON_STYLES[variant]})")
        tint = self.color(p.get("tint"), scope) or (self.color(p.get("background"), scope) if system else None)
        if tint:
            out_mods.append(f".tint({tint[0]})")
        r = self.number(p.get("radius"), self.ej.radius) if system else None
        if r:
            out_mods.append(".buttonBorderShape(.capsule)" if r.value >= 500
                            else f".buttonBorderShape(.roundedRectangle(radius: {r.expr}))")
        size = rt_string(p.get("controlSize"))
        if size in CONTROL_SIZES:
            out_mods.append(f".controlSize(.{size})")
        out_mods += self.behavior_mods(p, scope, allow_tap=False)
        return self.attach(lines, out_mods, ind)

    def button_label(self, node: dict, p: dict, scope: Scope, ind: int) -> list:
        pad = IND * ind
        if node.get("children"):
            sp = self.number(p.get("spacing"), self.ej.spacing)
            args = f"(spacing: {sp.expr})" if sp else ""
            return [pad + f"HStack{args} {{"] + self.children(node, scope, ind + 1) + [pad + "}"]
        title = self.text(p.get("text"), scope)
        symbol = self.text(p.get("symbol"), scope)
        if title is not None and symbol is not None:
            return [pad + f"Label({title.title()}, systemImage: {symbol.swift})"]
        if symbol is not None:
            return [pad + f"Image(systemName: {symbol.swift})"]
        return [pad + (title.text_view() if title is not None else 'Text("Button")')]

    # -- actions ---------------------------------------------------------------

    def actions(self, raw, scope: Scope, ind: int) -> list:
        out = []
        for a in flat_actions(raw):
            out.extend(IND * ind + line for line in self.action(a, scope))
        return out

    def action(self, a: dict, scope: Scope) -> list:
        ej = self.ej
        if a.get("navigate") is not None or a.get("sheet") is not None:
            verb = "navigate" if a.get("navigate") is not None else "sheet"
            target = rt_string(a.get(verb)) or ""
            if "{{" in target or target not in ej.routes:
                return [ej.todo(self.file, f"{verb} to {target!r}: no such screen")]
            r = ej.routes[target]
            given = a.get("params") if isinstance(a.get("params"), dict) else {}
            args = []
            for key, ident in r["params"]:
                if key in given:
                    tv = self.text(given[key], scope) or Text("", '""', True, True)
                    args.append(f"{ident}: {tv.swift}")
            route = f".{r['case']}" + (f"({', '.join(args)})" if args else "")
            return [f"router.{'navigate' if verb == 'navigate' else 'present'}({route})"]
        if a.get("dismiss") is not None:
            return ["router.dismiss()"]
        if a.get("tab") is not None:
            tid = rt_string(a.get("tab")) or ""
            tab = next((t for t in ej.tabs if t["id"] == tid), None)
            if tab is None:
                return [ej.todo(self.file, f"switch to tab {tid!r}: no such tab")]
            return [f"router.selectedTab = .{tab['case']}"]
        if a.get("toggle") is not None:
            key = rt_string(a.get("toggle")) or ""
            info = ej.state.get(key)
            if info is None or info["type"] != "Bool":
                return [ej.todo(self.file, f"toggle {key!r}: state is not a Bool")]
            return [f"state.{info['ident']}.toggle()"]
        if isinstance(a.get("set"), dict):
            out = []
            for key, value in a["set"].items():
                info = ej.state.get(str(key))
                if info is None or info["type"] == "Other":
                    out.append(ej.todo(self.file, f"set {key!r}: unsupported value"))
                    continue
                out.append(f"state.{info['ident']} = {self.state_value(value, info['type'], scope)}")
            return out
        if a.get("url") is not None:
            tv = self.text(a.get("url"), scope)
            if tv is None:
                return []
            self.uses_open_url = True
            return [f"if let url = URL(string: {tv.swift}) {{ openURL(url) }}"]
        if isinstance(a.get("alert"), dict):
            title = self.text(a["alert"].get("title"), scope) or Text("", '""', True, True)
            message = self.text(a["alert"].get("message"), scope) or Text("", '""', True, True)
            return [f"router.showAlert(title: {title.swift}, message: {message.swift})"]
        return []

    def state_value(self, value, typ: str, scope: Scope) -> str:
        if typ == "String":
            tv = self.text(value, scope) if isinstance(value, str) else Text(rt_string(value) or "", swift_str(
                rt_string(value) or ""), True, True)
            return tv.swift if tv else '""'
        if isinstance(value, str) and "{{" in value:
            tv = self.text(value, scope)
            if typ == "Bool":
                return f"AppState.truthy({tv.swift})"
            return f"{typ}({tv.swift}) ?? 0"
        if typ == "Bool":
            return "true" if rt_bool(value) else "false"
        d = rt_double(value)
        if d is None:
            return literal(default_for(typ), typ)
        return str(int(d)) if typ == "Int" else num(d)

    # -- modifiers (same order as LiveRenderer) -----------------------------------

    def text_mods(self, t: str, p: dict, scope: Scope) -> list:
        mods = []
        size, weight, design, style = self.ej.font_spec(p)
        font = self.ej.font_expr(size, weight, design, style)
        if font:
            tok = rt_string(p.get("font"))
            own = any(p.get(k) is not None for k in ("size", "weight", "design"))
            if tok and tok.startswith("$") and not own and tok[1:] in self.ej.font_names and \
                    self.ej.font_expr(*self.ej.font_spec({"font": tok})) == font:
                font = f"Theme.Fonts.{self.ej.font_names[tok[1:]]}"
            mods.append(f".font({font})")
        else:
            w = rt_string(p.get("weight"))
            if w in WEIGHTS:
                mods.append(f".fontWeight(.{w})")
            d = DESIGNS.get(rt_string(p.get("design")) or "")
            if d:
                mods.append(f".fontDesign({d})")
        fg = self.fill(p.get("foreground"), scope, "Color.primary")
        if fg:
            mods.append(f".foregroundStyle({fg})")
        if t != "button":
            tint = self.color(p.get("tint"), scope)
            if tint:
                mods.append(f".tint({tint[0]})")
        align = rt_string(p.get("textAlign"))
        if align in ("center", "trailing", "leading"):
            mods.append(f".multilineTextAlignment(.{align})")
        lines = rt_int(rt_string(p.get("lines")))
        if lines is not None and lines >= 1:
            mods.append(f".lineLimit({lines})")
        if rt_bool(rt_string(p.get("italic"))) is True:
            mods.append(".italic()")
        return mods

    def insets(self, raw) -> Optional[str]:
        sp = self.ej.spacing
        n = self.number(raw, sp)
        if n:
            return f".padding({n.expr})"
        if not isinstance(raw, dict):
            return None
        zero = Num("0", 0.0)
        all_ = self.number(raw.get("all"), sp) or zero
        h = self.number(raw.get("horizontal"), sp) or all_
        v = self.number(raw.get("vertical"), sp) or all_
        top = self.number(raw.get("top"), sp) or v
        bottom = self.number(raw.get("bottom"), sp) or v
        leading = self.number(raw.get("leading"), sp) or h
        trailing = self.number(raw.get("trailing"), sp) or h
        exprs = [top.expr, leading.expr, bottom.expr, trailing.expr]
        if len(set(exprs)) == 1:
            return None if top.value == 0 else f".padding({top.expr})"
        if top.value == 0 and bottom.value == 0 and leading.expr == trailing.expr:
            return f".padding(.horizontal, {leading.expr})"
        if leading.value == 0 and trailing.value == 0 and top.expr == bottom.expr:
            return f".padding(.vertical, {top.expr})"
        return (f".padding(EdgeInsets(top: {top.expr}, leading: {leading.expr}, bottom: {bottom.expr}, "
                f"trailing: {trailing.expr}))")

    def layout_mods(self, t: str, p: dict, scope: Scope) -> list:
        mods = []
        if t != "scroll":
            pad = self.insets(p.get("padding"))
            if pad:
                mods.append(pad)

        def frame_num(key, allow_inf=True):
            n = self.number(p.get(key), None, allow_inf)
            if n is not None and n.value < 0:
                return Num("0", 0.0)
            return n

        width, height = frame_num("width"), frame_num("height")
        min_w, max_w = frame_num("minWidth", False), frame_num("maxWidth")
        min_h, max_h = frame_num("minHeight", False), frame_num("maxHeight")
        if width is not None and math.isinf(width.value):
            max_w, width = Num(".infinity", math.inf), None
        if height is not None and math.isinf(height.value):
            max_h, height = Num(".infinity", math.inf), None
        align = ALIGN_9.get(rt_string(p.get("frameAlign")) or "", ".center")
        align_arg = [] if align == ".center" else [f"alignment: {align}"]
        if width is not None or height is not None:
            args = ([f"width: {width.expr}"] if width else []) + ([f"height: {height.expr}"] if height else [])
            mods.append(f".frame({', '.join(args + align_arg)})")
        raster = t == "image" and p.get("symbol") is None
        sized = any(x is not None for x in (width, height, max_w, max_h))
        if raster and sized:
            args = [f"minWidth: {min_w.expr if min_w else '0'}"] + ([f"maxWidth: {max_w.expr}"] if max_w else [])
            args += [f"minHeight: {min_h.expr if min_h else '0'}"] + ([f"maxHeight: {max_h.expr}"] if max_h else [])
            mods.append(f".frame({', '.join(args + align_arg)})")
        elif any(x is not None for x in (min_w, max_w, min_h, max_h)):
            args = ([f"minWidth: {min_w.expr}"] if min_w else []) + ([f"maxWidth: {max_w.expr}"] if max_w else [])
            args += ([f"minHeight: {min_h.expr}"] if min_h else []) + ([f"maxHeight: {max_h.expr}"] if max_h else [])
            mods.append(f".frame({', '.join(args + align_arg)})")
        if raster:
            mods.append(".clipped()")
        if isinstance(p.get("offset"), dict):
            x = self.number(p["offset"].get("x"))
            y = self.number(p["offset"].get("y"))
            mods.append(f".offset(x: {x.expr if x else '0'}, y: {y.expr if y else '0'})")
        return mods

    def decoration_mods(self, t: str, p: dict, scope: Scope) -> list:
        mods = []
        radius = self.number(p.get("radius"), self.ej.radius)
        bg = self.fill(p.get("background"), scope, "Color.clear")
        if bg:
            mods.append(f".background({bg})")
        if radius and t != "shape":
            mods.append(f".clipShape(RoundedRectangle(cornerRadius: {radius.expr}, style: .continuous))")
        if isinstance(p.get("border"), dict):
            c = self.color(p["border"].get("color"), scope)
            color = (f"({c[0]} ?? Color.gray.opacity(0.3))" if c[1] else c[0]) if c else "Color.gray.opacity(0.3)"
            w = self.number(p["border"].get("width"))
            mods.append(".overlay {\n" + IND + f"RoundedRectangle(cornerRadius: {radius.expr if radius else '0'}, "
                        f"style: .continuous).strokeBorder({color}, lineWidth: {w.expr if w else '1'})\n}}")
        if "glass" in p and p.get("glass") is not None:
            kind = rt_string(p.get("glass")) or "regular"
            if kind not in ("false", "none"):
                effect = ".clear" if kind == "clear" else ".regular"
                mods.append(f".glassEffect({effect}, in: RoundedRectangle(cornerRadius: "
                            f"{radius.expr if radius else '20'}, style: .continuous))")
        shadow = self.shadow_mod(p.get("shadow"))
        if shadow:
            mods.append(shadow)
        o = rt_double(rt_string(p.get("opacity")))
        if o is not None and math.isfinite(o):
            mods.append(f".opacity({num(min(max(o, 0), 1))})")
        if rt_bool(rt_string(p.get("ignoreSafeArea"))) is True:
            mods.append(".ignoresSafeArea()")
        return mods

    def shadow_mod(self, raw) -> Optional[str]:
        ej = self.ej
        if isinstance(raw, dict):
            return f".shadow({ej.shadow_expr(raw)})"
        s = rt_string(raw)
        if s is None or s == "none":
            return None
        name = s[1:] if s.startswith("$") else s
        spec = ej.shadows.get(name)
        if not isinstance(spec, dict):
            return None
        return f".shadow(Theme.Shadows.{ej.shadow_names[name]})"

    def behavior_mods(self, p: dict, scope: Scope, allow_tap: bool) -> list:
        mods = []
        if rt_bool(rt_string(p.get("disabled"))) is True:
            mods.append(".disabled(true)")
        if allow_tap and p.get("onTap") is not None:
            body = self.actions(p.get("onTap"), scope, 1)
            mods.append(".contentShape(Rectangle())")
            mods.append(".onTapGesture {\n" + "\n".join(body) + "\n}" if body else ".onTapGesture {}")
        return mods

    # -- the file --------------------------------------------------------------

    def swift(self) -> str:
        body_node = screen_body(self.sid, self.js)
        scope = Scope(self)
        if body_node is None:
            root = [IND * 2 + 'EjectPlaceholder("empty screen")']
        else:
            p = self.ej.effective(body_node)
            cond = self.condition(p, scope)
            if cond is False:
                root = [IND * 2 + "EmptyView()"]
            elif isinstance(cond, str):
                root = ([IND * 2 + "Group {", IND * 3 + f"if {cond} {{"]
                        + self.view(body_node, scope, 4, extract=False) + [IND * 3 + "}", IND * 2 + "}"])
            elif body_node.get("type") == "repeat":
                root = self.view(body_node, scope, 2, extract=False)
            else:
                root = self.view(body_node, scope, 2, extract=False)
        root += self.screen_mods(scope)
        L = [HEADER.format(file=self.file, source=f"design/screens/{self.sid}.json"), "import SwiftUI", "",
             f"struct {self.type}: View {{",
             f"{IND}@EnvironmentObject var state: AppState",
             f"{IND}@EnvironmentObject var router: Router"]
        if self.uses_open_url:
            L.append(f"{IND}@Environment(\\.openURL) var openURL")
        if self.params:
            L.append("")
            L.append(f"{IND}// Navigation params (nil when the screen is shown without them).")
            for _, ident in self.params.items():
                L.append(f"{IND}var {ident}: String? = nil")
        for const in self.constants:
            L.append("")
            L.extend(const)
        L += ["", f"{IND}var body: some View {{"] + root + [f"{IND}}}"]
        for member in self.members:
            L.append("")
            L.extend(member)
        L.append("}")
        for struct in self.structs:
            L.append("")
            L.extend(struct)
        preview_args = ""
        L += ["", f"struct {self.type}_Previews: PreviewProvider {{",
              f"{IND}static var previews: some View {{",
              f"{IND * 2}NavigationStack {{ {self.type}({preview_args}) }}",
              f"{IND * 3}.environmentObject(AppState())",
              f"{IND * 3}.environmentObject(Router())",
              f"{IND}}}", "}", ""]
        return "\n".join(L)

    def screen_mods(self, scope: Scope) -> list:
        js = self.js
        mods = [".frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)"]
        bg = self.fill(js.get("background"), scope, "Color.clear")
        if bg:
            mods.append(f".background({bg})")
        title = self.text(js.get("title"), scope) if isinstance(js.get("title"), str) else None
        mods.append(f".navigationTitle({title.title() if title else chr(34) * 2})")
        display = js.get("titleDisplay") if isinstance(js.get("titleDisplay"), str) else "large"
        if display == "inline":
            mods.append(".navigationBarTitleDisplayMode(.inline)")
        elif display == "hidden":
            mods.append(".toolbar(.hidden, for: .navigationBar)")
        else:
            mods.append(".navigationBarTitleDisplayMode(.large)")
        items = [n for n in (js.get("toolbar") or []) if isinstance(n, dict)]
        leading = [n for n in items if n.get("placement") == "leading"]
        trailing = [n for n in items if n.get("placement") != "leading"]
        groups = []
        for placement, group in ((".topBarLeading", leading), (".topBarTrailing", trailing)):
            if not group:
                continue
            lines = [IND + f"ToolbarItemGroup(placement: {placement}) {{"]
            for n in group:
                lines.extend(self.toolbar_item(n, scope, 2))
            lines.append(IND + "}")
            groups.append("\n".join(lines))
        if groups:
            mods.append(".toolbar {\n" + "\n".join(groups) + "\n}")
        return [line for m in mods for line in indent_block(m, 2)]

    def toolbar_item(self, node: dict, scope: Scope, ind: int) -> list:
        p = self.ej.effective(node)
        cond = self.condition(p, scope)
        if cond is False:
            return []
        inner = ind + (1 if isinstance(cond, str) else 0)
        pad = IND * inner
        title = self.text(p.get("text"), scope)
        symbol = self.text(p.get("symbol"), scope)
        if title is not None and symbol is not None:
            label = f"Label({title.title()}, systemImage: {symbol.swift})"
        elif symbol is not None:
            label = f"Image(systemName: {symbol.swift})"
        else:
            label = title.text_view() if title is not None else 'Text("Button")'
        action = p.get("action") if p.get("action") is not None else p.get("onTap")
        lines = [pad + "Button {"] + self.actions(action, scope, inner + 1) + [pad + "} label: {",
                                                                             pad + IND + label, pad + "}"]
        tint = self.color(p.get("tint"), scope) or self.color(p.get("foreground"), scope)
        if tint:
            lines.append(pad + f".tint({tint[0]})")
        if isinstance(cond, str):
            return [IND * ind + f"if {cond} {{"] + lines + [IND * ind + "}"]
        return lines


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


def screen_body(sid: str, js: dict):
    """The screen's root node; a bare list of nodes becomes a padded scrolling column
    (LiveScreen does the same)."""
    body = js.get("body")
    if isinstance(body, list):
        if len(body) == 1 and isinstance(body[0], dict) and body[0].get("type") in ("list", "scroll"):
            return body[0]
        return {"type": "scroll", "id": f"{sid}.body", "spacing": 16, "padding": 16, "children": body}
    return body if isinstance(body, dict) else None


def hex_color(s: str) -> Optional[str]:
    h = s[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
        return None
    if len(h) == 8:
        alpha = int(h[6:], 16) / 255
        return f"Color(hex: 0x{h[:6].upper()}, opacity: {num(round(alpha, 3))})"
    return f"Color(hex: 0x{h.upper()})"


def swift_type_of(v) -> str:
    if isinstance(v, bool):
        return "Bool"
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return "Int"
    if isinstance(v, float):
        return "Double"
    if isinstance(v, str):
        return "String"
    return "Other"


def merged_type(values: list) -> Optional[str]:
    kinds = {swift_type_of(v) for v in values}
    if not kinds or "Other" in kinds:
        return None if "Other" in kinds else "String"
    if kinds == {"Bool"}:
        return "Bool"
    if kinds == {"Int"}:
        return "Int"
    if kinds <= {"Int", "Double"}:
        return "Double"
    return "String"


def default_for(typ: str):
    return {"Bool": False, "Int": 0, "Double": 0.0, "String": ""}.get(typ, "")


def literal(v, typ: str) -> str:
    if typ == "Bool":
        return "true" if rt_bool(v) else "false"
    if typ == "Int":
        d = rt_double(v)
        return str(int(d)) if d is not None and math.isfinite(d) else "0"
    if typ == "Double":
        d = rt_double(v)
        return num(d) if d is not None and math.isfinite(d) else "0"
    return swift_str(rt_string(v) or "")


def truthy(expr: str, typ: str) -> str:
    if typ == "Bool":
        return expr
    if typ in ("Int", "Double"):
        return f"{expr} != 0"
    return f"AppState.truthy({expr})"


def compare(expr: str, typ: str, op: str, rhs: str):
    """`state.key == "value"` with the runtime's text comparison, typed."""
    if typ == "String":
        return f"{expr} {op} {swift_str(rhs)}"
    if typ == "Bool":
        if rhs not in ("true", "false"):
            return op == "!="
        positive = (rhs == "true") == (op == "==")
        return expr if positive else f"!{expr}"
    d = rt_double(rhs)
    if d is None or not math.isfinite(d):
        return op == "!="
    if typ == "Int" and not d.is_integer():
        return op == "!="
    return f"{expr} {op} {str(int(d)) if typ == 'Int' else num(d)}"


HEADER = "// {file} — generated by `./ui eject` from {source}.\n// Plain SwiftUI: edit freely (re-ejecting will not overwrite files you changed)."

THEME_SUPPORT = """struct ThemeShadow {
    let color: Color
    let radius: CGFloat
    let x: CGFloat
    let y: CGFloat
}

extension View {
    func shadow(_ s: ThemeShadow) -> some View {
        shadow(color: s.color, radius: s.radius, x: s.x, y: s.y)
    }
}

extension Color {
    /// 0xRRGGBB in sRGB.
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }

    /// "#RGB", "#RRGGBB" or "#RRGGBBAA".
    init?(hexString: String) {
        var h = String(hexString.dropFirst())
        if h.count == 3 { h = h.map { "\\($0)\\($0)" }.joined() }
        guard h.count == 6 || h.count == 8, let value = UInt64(h, radix: 16) else { return nil }
        if h.count == 8 {
            self.init(hex: UInt32(value >> 8), opacity: Double(value & 0xFF) / 255)
        } else {
            self.init(hex: UInt32(value))
        }
    }

    /// A color that follows light / dark mode.
    init(light: Color, dark: Color) {
        self.init(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
    }
}
"""

STATE_SUPPORT = """    /// visibleIf rules: "key", "!key", "key==value", "key!=value".
    func isVisible(_ condition: String) -> Bool {
        let c = condition.trimmingCharacters(in: .whitespaces)
        for op in ["!=", "=="] {
            if let r = c.range(of: op) {
                let lhs = c[c.startIndex..<r.lowerBound].trimmingCharacters(in: .whitespaces)
                let rhs = c[r.upperBound...].trimmingCharacters(in: .whitespaces)
                let value = string(forKey: lhs) ?? lhs
                return op == "==" ? value == rhs : value != rhs
            }
        }
        let negate = c.hasPrefix("!")
        let key = negate ? String(c.dropFirst()) : c
        let result = AppState.truthy(string(forKey: key) ?? key)
        return negate ? !result : result
    }

    /// "true" / "yes" / "1" and other non-empty words are true; "false", "no", "0" and "" are false.
    static func truthy(_ s: String) -> Bool {
        !["false", "no", "0", ""].contains(s.lowercased())
    }"""

ROUTER_SUPPORT = """    func showAlert(title: String, message: String) {
        alertTitle = title
        alertMessage = message
        alertShown = true
    }

    /// The alert shows on the sheet while one is presented, else on the root view.
    func alertBinding(inSheet: Bool) -> Binding<Bool> {
        Binding(get: { self.alertShown && (self.sheet != nil) == inSheet },
                set: { if !$0 { self.alertShown = false } })
    }"""

SUPPORT_SWIFT = """// Support.swift — small views used by the generated screens.
import SwiftUI

/// An image loaded from a URL, with the prototype's loading and error placeholders.
struct RemoteImage: View {
    let url: String
    var aspect: CGFloat? = nil
    var mode: ContentMode = .fill

    var body: some View {
        AsyncImage(url: URL(string: url)) { phase in
            if let image = phase.image {
                image.resizable().aspectRatio(aspect, contentMode: mode)
            } else if phase.error != nil {
                ZStack {
                    Color.gray.opacity(0.2)
                    Image(systemName: "photo").foregroundStyle(.secondary)
                }
            } else {
                Color.gray.opacity(0.12)
            }
        }
    }
}

/// Marks something the generator could not translate (see README.md, "Left to do").
struct EjectPlaceholder: View {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var body: some View {
        Text(message)
            .font(.caption.monospaced())
            .foregroundStyle(.red)
            .padding(6)
            .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}
"""

RUN_SH = """#!/usr/bin/env bash
# Build + launch this app on the iOS Simulator (generated by ./ui eject).
# Usage: ./run.sh [launch args, e.g. -initialTab explore]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
SCHEME="__APP_NAME__"
BUNDLE_ID="__BUNDLE_ID__"
UDID="$(xcrun simctl list devices available -j | python3 -c '
import json, re, sys
best = None
for runtime, devices in json.load(sys.stdin)["devices"].items():
    m = re.search(r"iOS-(\\d+)-(\\d+)", runtime)
    if not m or int(m.group(1)) < 26:
        continue
    for d in devices:
        if d["name"].startswith("iPhone"):
            key = (d["state"] == "Booted", (int(m.group(1)), int(m.group(2))), d["name"] == "iPhone 17 Pro")
            if best is None or key > best[0]:
                best = (key, d["udid"])
print(best[1] if best else "")')"
[ -n "$UDID" ] || { echo "No iOS 26+ iPhone simulator found."; exit 1; }
xcrun simctl boot "$UDID" >/dev/null 2>&1 || true
open -a Simulator >/dev/null 2>&1 || true
xcodegen generate >/dev/null
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then ARCH=arm64; else ARCH=x86_64; fi
xcodebuild -project "$SCHEME.xcodeproj" -scheme "$SCHEME" -configuration Debug \\
  -destination 'generic/platform=iOS Simulator' -derivedDataPath build \\
  ARCHS="$ARCH" ONLY_ACTIVE_ARCH=YES COMPILER_INDEX_STORE_ENABLE=NO build 2>&1 \\
  | grep -E "error:|warning: |BUILD (SUCCEEDED|FAILED)" || true
APP="build/Build/Products/Debug-iphonesimulator/$SCHEME.app"
[ -d "$APP" ] || { echo "Build failed."; exit 1; }
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl install "$UDID" "$APP"
xcrun simctl launch --terminate-running-process "$UDID" "$BUNDLE_ID" "$@" >/dev/null
echo ">> $SCHEME is running on $UDID"
"""


# --------------------------------------------------------------------------
# Writing files safely
# --------------------------------------------------------------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EjectError(Exception):
    pass


def eject(design_dir: Path, out_dir: Path, name: str, bundle_id: str, force: bool = False) -> dict:
    """Generates the app into `out_dir`. Refuses to overwrite files changed since the
    last eject (or a non-empty folder that wasn't ejected) unless `force`."""
    out_dir = Path(out_dir)
    ej = Ejector(design_dir, name, bundle_id)
    files = ej.generate()
    manifest_path = out_dir / MANIFEST
    old = {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
        except (OSError, ValueError):
            old = {}
    elif out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise EjectError(f"{out_dir} exists and was not created by ./ui eject; pass --force or choose --out")
    changed = []
    for rel, digest in old.items():
        path = out_dir / rel
        if path.exists() and sha(path.read_text(encoding="utf-8", errors="replace")) != digest:
            changed.append(rel)
    if changed and not force:
        raise EjectError("these files changed since the last eject (pass --force to overwrite):\n  "
                         + "\n  ".join(sorted(changed)))
    removed = []
    for rel in old:
        if rel not in files and (out_dir / rel).exists() and (force or rel not in changed):
            (out_dir / rel).unlink()
            removed.append(rel)
    for rel, text in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if rel == "run.sh":
            path.chmod(0o755)
    manifest_path.write_text(json.dumps({"generator": GENERATOR, "files": {r: sha(t) for r, t in files.items()}},
                                        indent=2) + "\n", encoding="utf-8")
    return {"out": str(out_dir), "files": sorted(files), "removed": removed, "todos": ej.todos,
            "app": ej.app_type}
