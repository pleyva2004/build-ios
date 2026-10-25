"""Tests for templates/tools/eject.py: design JSON -> standalone SwiftUI app.

    python3 -m unittest discover -s tests -v

The Swift output is compiled and screenshot-compared with the prototype in CI
(.github/workflows/ios.yml); these tests check the translation itself, the
file-safety rules, and that the generator keeps up with the CLI's vocabulary.
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


def load(name, path):
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = old


ej = load("eject_under_test", TEMPLATES / "tools" / "eject.py")
ui = load("ui_for_eject_tests", TEMPLATES / "ui")


def swift_balance(src: str) -> str:
    """'ok' when (), [], {} and string literals (with \\(...) interpolation) balance."""
    stack, modes, i, n = [], ["code"], 0, len(src)
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < n:
        c = src[i]
        if modes[-1] == "string":
            if c == "\\" and src[i + 1:i + 2] == "(":
                modes.append("interp")
                stack.append("I(")
                i += 2
                continue
            if c == "\\":
                i += 2
                continue
            if c == '"':
                modes.pop()
            elif c == "\n":
                return f"unterminated string at {i}"
            i += 1
            continue
        if c == "/" and src[i + 1:i + 2] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"':
            modes.append("string")
        elif c in "([{":
            stack.append(c)
        elif c in ")]}":
            if not stack:
                return f"unbalanced {c} at {i}"
            top = stack.pop()
            if top == "I(":
                if c != ")":
                    return f"bad interpolation at {i}"
                modes.pop()
            elif top != pairs[c]:
                return f"mismatched {c} at {i}"
        i += 1
    return "ok" if not stack and modes == ["code"] else f"unclosed {stack[-3:]}"


class EjectCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="eject-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.design = self.root / "Demo" / "design"
        shutil.copytree(str(TEMPLATES / "design"), str(self.design))
        app = self.design / "app.json"
        app.write_text(app.read_text(encoding="utf-8").replace("__APP_NAME__", "Demo"), encoding="utf-8")
        self.out = self.root / "DemoSwiftUI"

    def edit(self, rel, fn):
        path = self.design / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        fn(data)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def run_eject(self, **kw):
        return ej.eject(self.design, self.out, "Demo", "com.example.Demo.swiftui", **kw)

    def files(self):
        self.run_eject()
        return {str(p.relative_to(self.out)): p.read_text(encoding="utf-8")
                for p in self.out.rglob("*") if p.is_file()}

    def screen(self, name):
        return (self.out / "Sources" / "Screens" / f"{name}Screen.swift").read_text(encoding="utf-8")


class OutputTest(EjectCase):
    def test_files_and_report(self):
        report = self.run_eject()
        self.assertEqual(report["todos"], [])
        self.assertEqual(report["app"], "Demo")
        for rel in ("project.yml", "run.sh", "README.md", "Sources/DemoApp.swift", "Sources/RootView.swift",
                    "Sources/Router.swift", "Sources/AppState.swift", "Sources/Theme.swift", "Sources/Support.swift",
                    "Sources/Screens/HomeScreen.swift", "Sources/Screens/ExploreScreen.swift",
                    "Sources/Screens/ProfileScreen.swift", "Sources/Screens/StoryScreen.swift",
                    "Sources/Screens/ComposeScreen.swift", ".eject.json"):
            self.assertTrue((self.out / rel).exists(), rel)
        self.assertTrue(os.access(self.out / "run.sh", os.X_OK))
        yml = (self.out / "project.yml").read_text()
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER: com.example.Demo.swiftui", yml)
        self.assertNotIn("design", yml.split("sources:")[1].split("settings:")[0])  # no JSON bundled

    def test_every_swift_file_is_balanced_and_has_no_leftovers(self):
        for rel, text in self.files().items():
            if not rel.endswith(".swift"):
                continue
            self.assertEqual(swift_balance(text), "ok", rel)
            code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)
            for leak in ("None", "True", "False", "{{", "}}"):
                self.assertNotIn(leak, code, f"{rel}: {leak}")

    def test_deterministic(self):
        first = self.files()
        shutil.rmtree(self.out)
        self.assertEqual(self.files(), first)

    def test_home_screen(self):
        self.run_eject()
        s = self.screen("Home")
        for snippet in (
            "struct HomeScreen: View {",
            "let stories: [HomeStory] = [",
            'HomeStory(title: "Design systems that scale", author: "Ada Park", minutes: "6", '
            'image: "https://picsum.photos/seed/scale/800/500"),',
            "ScrollView(.vertical, showsIndicators: false) {",
            "VStack(spacing: Theme.Spacing.md) {",
            "ForEach(Array(stories.enumerated()), id: \\.offset) { index, item in",
            "story(item, index: index)",
            "private func story(_ item: HomeStory, index: Int) -> some View {",
            'Text("\\(item.author) · \\(item.minutes) min")',
            "RemoteImage(url: item.image)",
            ".frame(width: 72, height: 72)",
            ".frame(minWidth: 0, minHeight: 0)",
            ".clipped()",
            ".clipShape(RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous))",
            ".shadow(Theme.Shadows.raised)",
            "router.navigate(.story(author: item.author, image: item.image, title: item.title))",
            'Label("Start reading", systemImage: "book")',
            ".buttonStyle(.borderedProminent)",
            ".controlSize(.large)",
            "router.present(.compose)",
            "ToolbarItemGroup(placement: .topBarTrailing) {",
            '.navigationTitle("Home")',
            ".background(Color.background)",
            "struct HomeStory: Hashable {",
        ):
            self.assertIn(snippet, s)

    def test_modifier_order_matches_the_runtime(self):
        """text -> layout -> decoration -> behavior, as in LiveRenderer.render()."""
        self.run_eject()
        hero = self.screen("Home").split("private var hero: some View {")[1]
        order = [".padding(Theme.Spacing.md)", ".frame(maxWidth: .infinity, alignment: .leading)",
                 ".background(Color.surface)", ".clipShape(", ".shadow(Theme.Shadows.raised)"]
        positions = [hero.index(o) for o in order]
        self.assertEqual(positions, sorted(positions))

    def test_explore_values_from_sample_data(self):
        self.run_eject()
        s = self.screen("Explore")
        self.assertIn("Image(systemName: item.symbol)", s)
        self.assertIn(".font(Font.system(size: 28))", s)
        self.assertEqual(s.count("size: 28"), 1)  # one font, so weight/design can combine with the size
        self.assertIn(".background(Theme.color(item.color) ?? Color.clear)", s)
        self.assertIn("count: 2), spacing: Theme.Spacing.md) {", s)
        self.assertIn('TextField("Search topics", text: $state.query)', s)
        self.assertIn(".textFieldStyle(.roundedBorder)", s)
        self.assertIn(".frame(height: 120, alignment: .topLeading)", s)

    def test_profile(self):
        self.run_eject()
        s = self.screen("Profile")
        for snippet in (
            "List {", ".listStyle(.insetGrouped)", 'Toggle("Notifications", isOn: $state.notifications)',
            'Picker("Plan", selection: $state.plan) {', 'ForEach(["Monthly", "Yearly"], id: \\.self)',
            ".pickerStyle(.segmented)", "} header: {", 'Text("Preferences")', "} footer: {",
            'Text("Version 1.0")', "@Environment(\\.openURL) var openURL",
            'if let url = URL(string: "https://example.com/help") { openURL(url) }',
            "Button(role: .destructive) {", 'router.showAlert(title: "Signed out", message: "This is a demo.")',
            ".foregroundStyle(Color.brand)",
        ):
            self.assertIn(snippet, s)

    def test_story_reads_its_params(self):
        self.run_eject()
        s = self.screen("Story")
        self.assertIn("var author: String? = nil", s)
        self.assertIn("var title: String? = nil", s)
        self.assertIn('Text(title ?? "")', s)
        self.assertIn('Label(author ?? "", systemImage: "person.circle")', s)
        self.assertIn('RemoteImage(url: image ?? "")', s)
        self.assertIn(".padding(.horizontal, Theme.Spacing.md)", s)
        self.assertIn(".navigationBarTitleDisplayMode(.inline)", s)

    def test_router_and_root(self):
        files = self.files()
        r = files["Sources/Router.swift"]
        self.assertIn("case story(author: String? = nil, image: String? = nil, title: String? = nil)", r)
        self.assertIn("case compose\n", r)
        self.assertIn("StoryScreen(author: author, image: image, title: title)", r)
        self.assertIn('case home = "home"', r)
        self.assertIn("@Published var selectedTab: AppTab = .home", r)
        root = files["Sources/RootView.swift"]
        self.assertIn('.tabItem { Label("Explore", systemImage: "square.grid.2x2") }', root)
        self.assertIn(".tint(Color.brand)", root)
        self.assertIn(".sheet(item: $router.sheet) { route in", root)

    def test_theme(self):
        t = self.files()["Sources/Theme.swift"]
        self.assertIn("static let brand = Color(hex: 0x5B5BD6)", t)
        self.assertIn("static let background = Color(light: Color(hex: 0xF6F6F9), dark: Color(hex: 0x0B0B10))", t)
        self.assertIn("static let title = Font.system(size: 22, weight: .bold)", t)
        self.assertIn("static let md: CGFloat = 16", t)
        self.assertIn("static let pill: CGFloat = 999", t)
        self.assertIn("static let raised = ThemeShadow(color: Color(hex: 0x000000).opacity(0.08), radius: 12, x: 0, y: 4)", t)
        self.assertIn('case "brand": return Color.brand', t)

    def test_state(self):
        s = self.files()["Sources/AppState.swift"]
        self.assertIn("@Published var notifications: Bool = true", s)
        self.assertIn('@Published var plan: String = "Monthly"', s)
        self.assertIn('@Published var draftTitle: String = ""', s)  # from the compose text field


class TranslationTest(EjectCase):
    def body(self, nodes, screen="home", **extra):
        def fn(d):
            d["body"] = nodes
            d.update(extra)
        self.edit(f"screens/{screen}.json", fn)
        self.run_eject()
        return self.screen(screen.capitalize())

    def test_visible_if(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "text", "id": "home.a", "text": "A", "visibleIf": "notifications"},
            {"type": "text", "id": "home.b", "text": "B", "visibleIf": "!notifications"},
            {"type": "text", "id": "home.c", "text": "C", "visibleIf": "plan==Yearly"},
            {"type": "text", "id": "home.d", "text": "D", "visibleIf": "false"},
            {"type": "text", "id": "home.e", "text": "E", "visibleIf": "query"},
            {"type": "text", "id": "home.f", "text": "F", "visibleIf": "nope==x"},
        ]})
        self.assertIn("if state.notifications {", s)
        self.assertIn("if !state.notifications {", s)
        self.assertIn('if state.plan == "Yearly" {', s)
        self.assertNotIn('Text("D")', s)
        self.assertIn("if AppState.truthy(state.query) {", s)
        self.assertNotIn('Text("F")', s)

    def test_state_inference(self):
        self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "button", "id": "home.t", "text": "T", "action": {"toggle": "expanded"}},
            {"type": "button", "id": "home.s", "text": "S", "action": [{"set": {"count": 3, "mode": "grid"}},
                                                                       {"tab": "explore"}]},
            {"type": "picker", "id": "home.p", "bind": "size", "options": ["S", "M"]},
            {"type": "text", "id": "home.x", "text": "{{mode}} mode", "visibleIf": "showMode"},
        ]})
        state = (self.out / "Sources" / "AppState.swift").read_text()
        self.assertIn("@Published var expanded: Bool = false", state)
        self.assertIn("@Published var count: Int = 0", state)
        self.assertIn('@Published var mode: String = ""', state)
        self.assertIn('@Published var size: String = "S"', state)  # first option, like the runtime
        self.assertIn("@Published var showMode: Bool = false", state)
        home = self.screen("Home")
        self.assertIn("state.expanded.toggle()", home)
        self.assertIn('state.count = 3', home)
        self.assertIn('state.mode = "grid"', home)
        self.assertIn("router.selectedTab = .explore", home)
        self.assertIn('Text("\\(state.mode) mode")', home)

    def test_bare_body_list_becomes_a_padded_scroll(self):
        s = self.body([{"type": "text", "id": "home.a", "text": "Hi"}])
        self.assertIn("ScrollView(.vertical, showsIndicators: false) {", s)
        self.assertIn("VStack(spacing: 16) {", s)
        self.assertIn(".padding(16)", s)

    def test_markdown_and_format_characters_render_verbatim(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "text", "id": "home.a", "text": "50% *off*"},
            {"type": "text", "id": "home.b", "text": "Plain words"},
            {"type": "toggle", "id": "home.c", "text": "a_b", "bind": "ab"},
        ]})
        self.assertIn('Text(verbatim: "50% *off*")', s)
        self.assertIn('Text("Plain words")', s)
        self.assertIn('Toggle("a_b" as String, isOn: $state.ab)', s)

    def test_symbol_size_and_weight_combine(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "image", "id": "home.icon", "symbol": "star", "size": 16, "weight": "semibold"}]})
        self.assertIn(".font(Font.system(size: 16, weight: .semibold))", s)

    def test_emails_and_urls_are_not_turned_into_links(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "text", "id": "home.a", "text": "jordan@example.com"},
            {"type": "text", "id": "home.b", "text": "https://example.com"},
        ]})
        self.assertIn('Text(verbatim: "jordan@example.com")', s)
        self.assertIn('Text(verbatim: "https://example.com")', s)

    def test_custom_and_unknown_nodes_become_placeholders_with_todos(self):
        report = None
        self.edit("screens/home.json", lambda d: d.update(body={"type": "vstack", "id": "home.root", "children": [
            {"type": "custom", "id": "home.stars", "name": "RatingStars", "rating": 4.5}]}))
        report = self.run_eject()
        s = self.screen("Home")
        self.assertIn('EjectPlaceholder("RatingStars")', s)
        self.assertTrue(any("RatingStars" in t for t in report["todos"]))
        self.assertIn("RatingStars", (self.out / "README.md").read_text())

    def test_buttons(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "button", "id": "home.a", "text": "Go", "variant": "secondary", "radius": "$pill",
             "background": "teal", "action": {"dismiss": True}},
            {"type": "button", "id": "home.b", "symbol": "plus", "variant": "glass", "radius": 10},
            {"type": "button", "id": "home.c", "text": "Plain", "background": "red", "radius": 6,
             "disabled": True},
        ]})
        self.assertIn(".buttonStyle(.bordered)", s)
        self.assertIn(".tint(Color.teal)", s)  # background becomes the tint of a system button
        self.assertIn(".buttonBorderShape(.capsule)", s)
        self.assertIn(".buttonBorderShape(.roundedRectangle(radius: 10))", s)
        self.assertIn(".buttonStyle(.glass)", s)
        self.assertIn("router.dismiss()", s)
        plain = s.split('Text("Plain")')[1]
        self.assertIn(".background(Color.red)", plain)  # no variant: background stays on the label
        self.assertIn(".disabled(true)", plain)

    def test_gradient_border_glass_offset(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "text", "id": "home.a", "text": "A", "background": {"gradient": ["$brand", "#000"],
                                                                        "direction": "right"},
             "border": {"color": "$border", "width": 2}, "radius": "$sm", "glass": "clear",
             "offset": {"x": 4}, "opacity": 0.5, "italic": True, "textAlign": "center", "lines": 3},
        ]})
        self.assertIn(".background(LinearGradient(colors: [Color.brand, Color(hex: 0x000000)], "
                      "startPoint: .leading, endPoint: .trailing))", s)
        self.assertIn("RoundedRectangle(cornerRadius: Theme.Radius.sm, style: .continuous)"
                      ".strokeBorder(Color.border, lineWidth: 2)", s)
        self.assertIn(".glassEffect(.clear, in: RoundedRectangle(cornerRadius: Theme.Radius.sm, style: .continuous))", s)
        self.assertIn(".offset(x: 4, y: 0)", s)
        self.assertIn(".opacity(0.5)", s)
        self.assertIn(".italic()", s)
        self.assertIn(".multilineTextAlignment(.center)", s)
        self.assertIn(".lineLimit(3)", s)

    def test_identifiers_are_swift_safe(self):
        def add(d):
            d["tabs"].append({"id": "default", "title": "Default", "symbol": "gear", "screen": "my-settings"})
            d["state"]["draft.title"] = "x"
        self.edit("app.json", add)
        (self.design / "screens" / "my-settings.json").write_text(json.dumps(
            {"id": "my-settings", "title": "Settings", "body": {"type": "text", "id": "my-settings.t",
                                                                 "text": "{{draft.title}}"}}))
        self.run_eject()
        router = (self.out / "Sources" / "Router.swift").read_text()
        self.assertIn('case defaultValue = "default"', router)
        self.assertTrue((self.out / "Sources" / "Screens" / "MySettingsScreen.swift").exists())
        state = (self.out / "Sources" / "AppState.swift").read_text()
        self.assertIn('@Published var draftTitle: String = "x"', state)

    def test_repeat_count_and_scalars(self):
        s = self.body({"type": "vstack", "id": "home.root", "children": [
            {"type": "repeat", "id": "home.dots", "items": 3, "template": {"type": "text", "id": "home.dot",
                                                                         "text": "{{number}}"}},
            {"type": "repeat", "id": "home.tags", "items": ["a", "b"],
             "template": {"type": "text", "id": "home.tag", "text": "#{{value}}"}},
        ]})
        self.assertIn("ForEach(0..<3, id: \\.self) { index in", s)
        self.assertIn('Text(verbatim: "\\(index + 1)")', s)
        self.assertIn("struct HomeTag: Hashable {", s)
        self.assertIn('HomeTag(value: "a"),', s)


class SafetyTest(EjectCase):
    def test_changed_files_are_not_overwritten(self):
        self.run_eject()
        theme = self.out / "Sources" / "Theme.swift"
        theme.write_text(theme.read_text() + "\n// mine\n")
        with self.assertRaises(ej.EjectError) as cm:
            self.run_eject()
        self.assertIn("Sources/Theme.swift", str(cm.exception))
        self.assertIn("// mine", theme.read_text())
        self.run_eject(force=True)
        self.assertNotIn("// mine", theme.read_text())

    def test_own_files_are_kept_and_removed_screens_cleaned_up(self):
        self.run_eject()
        mine = self.out / "Sources" / "Mine.swift"
        mine.write_text("// mine\n")
        (self.design / "screens" / "compose.json").unlink()
        self.edit("screens/home.json", lambda d: d.update(toolbar=[]))
        report = self.run_eject()
        self.assertIn("Sources/Screens/ComposeScreen.swift", report["removed"])
        self.assertFalse((self.out / "Sources" / "Screens" / "ComposeScreen.swift").exists())
        self.assertTrue(mine.exists())

    def test_refuses_a_folder_it_did_not_create(self):
        self.out.mkdir()
        (self.out / "notes.txt").write_text("hi")
        with self.assertRaises(ej.EjectError):
            self.run_eject()
        self.run_eject(force=True)
        self.assertTrue((self.out / "notes.txt").exists())


class CliTest(EjectCase):
    def setUp(self):
        super().setUp()
        proj = self.root / "Demo"
        shutil.copy2(str(TEMPLATES / "ui"), str(proj / "ui"))
        (proj / "tools").mkdir()
        for f in ("REFERENCE.md", "eject.py"):
            shutil.copy2(str(TEMPLATES / "tools" / f), str(proj / "tools" / f))
        (proj / "project.yml").write_text((TEMPLATES / "project.yml").read_text()
                                          .replace("__APP_NAME__", "Demo")
                                          .replace("__BUNDLE_PREFIX__", "com.acme")
                                          .replace("__BUNDLE_ID__", "com.acme.Demo"))
        self.proj = proj

    def cli(self, *args):
        env = dict(os.environ, UI_PROJECT_DIR=str(self.proj), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(self.proj / "ui"), "eject", "--no-xcodegen"] + list(args),
                              cwd=str(self.proj), env=env, capture_output=True, text=True, timeout=60)

    def test_default_output_next_to_the_project(self):
        p = self.cli()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ok  ejected", p.stdout)
        yml = (self.root / "DemoSwiftUI" / "project.yml").read_text()
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER: com.acme.Demo.swiftui", yml)
        self.assertFalse((self.proj / "tools" / "__pycache__").exists())

    def test_invalid_design_is_refused(self):
        self.edit("screens/home.json", lambda d: d["body"].update(type="txt"))
        p = self.cli()
        self.assertEqual(p.returncode, 1)
        self.assertIn("the design has errors", p.stderr)
        self.assertFalse((self.root / "DemoSwiftUI").exists())

    def test_refuses_to_write_into_the_prototype(self):
        p = self.cli("--out", str(self.proj / "design" / "x"))
        self.assertEqual(p.returncode, 1)
        p = self.cli("--out", str(self.proj))
        self.assertEqual(p.returncode, 1)


class DriftTest(unittest.TestCase):
    def test_handles_every_prop_the_cli_knows(self):
        known = set(ui.COMMON_PROPS)
        for props in ui.TYPE_PROPS.values():
            known |= props
        missing = known - ej.HANDLED_PROPS
        self.assertEqual(missing, set(), "teach eject.py these props (or list them in HANDLED_PROPS)")

    def test_translates_every_node_type(self):
        source = (TEMPLATES / "tools" / "eject.py").read_text()
        for t in ui.NODE_TYPES:
            self.assertRegex(source, rf'(t|node.get\("type"\)) == "{t}"|"{t}"', t)
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp) / "design"
            shutil.copytree(str(TEMPLATES / "design"), str(design))
            nodes = [{"type": t, "id": f"home.n{i}", "text": "x", "name": "X",
                      **({"template": {"type": "text", "id": f"home.t{i}", "text": "y"}, "items": 1}
                         if t == "repeat" else {})}
                     for i, t in enumerate(sorted(ui.NODE_TYPES))]
            home = json.loads((design / "screens" / "home.json").read_text())
            home["body"] = {"type": "vstack", "id": "home.all", "children": nodes}
            (design / "screens" / "home.json").write_text(json.dumps(home))
            report = ej.eject(design, Path(tmp) / "out", "Demo", "com.example.Demo.swiftui")
            unknown = [t for t in report["todos"] if "unknown node type" in t]
            self.assertEqual(unknown, [])
            src = (Path(tmp) / "out" / "Sources" / "Screens" / "HomeScreen.swift").read_text()
            self.assertEqual(swift_balance(src), "ok")


if __name__ == "__main__":
    unittest.main()
