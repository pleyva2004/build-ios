"""Tests for templates/ui, the live-UI engine CLI (stdlib unittest only, Python 3.9+).

    python3 -m unittest discover -s tests -v

Every test works on a fresh copy of templates/design (+ REFERENCE.md and the ui
script) in a temp dir pointed to by UI_PROJECT_DIR; the real templates/ folder is
only read. The drift tests read templates/Runtime/*.swift to keep the CLI's
validator in step with what the Swift renderer actually understands.
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
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
UI_PATH = TEMPLATES / "ui"
RUNTIME = TEMPLATES / "Runtime"
REFERENCE = TEMPLATES / "tools" / "REFERENCE.md"


def load_ui():
    """Imports templates/ui (no .py extension) without writing templates/__pycache__."""
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader = importlib.machinery.SourceFileLoader("ui", str(UI_PATH))
        spec = importlib.util.spec_from_loader("ui", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = old


ui = load_ui()


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def wait_until(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


class ProjectCase(unittest.TestCase):
    """A fresh prototype project per test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ui-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        shutil.copytree(str(TEMPLATES / "design"), str(self.root / "design"))
        (self.root / "tools").mkdir()
        shutil.copy2(str(REFERENCE), str(self.root / "tools" / "REFERENCE.md"))
        shutil.copy2(str(UI_PATH), str(self.root / "ui"))
        app = self.root / "design" / "app.json"
        app.write_text(app.read_text(encoding="utf-8").replace("__APP_NAME__", "Demo"), encoding="utf-8")
        old = os.environ.get("UI_PROJECT_DIR")
        os.environ["UI_PROJECT_DIR"] = str(self.root)
        self.addCleanup(self._restore_env, old)
        self.project = ui.Project(self.root)
        self.live = ui.Live(self.project)

    @staticmethod
    def _restore_env(old):
        if old is None:
            os.environ.pop("UI_PROJECT_DIR", None)
        else:
            os.environ["UI_PROJECT_DIR"] = old

    # helpers ---------------------------------------------------------------

    def apply(self, ops, **kw):
        kw.setdefault("wait", False)
        kw.setdefault("quiet", True)
        return ui.run_ops(self.project, ops if isinstance(ops, list) else [ops], **kw)

    def design(self):
        return ui.Design(self.project)

    def node(self, nid):
        return self.design().find(nid).node

    def screen(self, sid):
        return json.loads((self.root / "design" / "screens" / (sid + ".json")).read_text(encoding="utf-8"))

    def tokens(self):
        return json.loads((self.root / "design" / "tokens.json").read_text(encoding="utf-8"))

    def child_ids(self, nid):
        return [c.get("id") for c in self.node(nid).get("children", [])]

    def design_bytes(self):
        d = self.root / "design"
        return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}

    def assertRejected(self, ops, *fragments):
        """The ops fail with all `fragments` in the message, and no file changes."""
        before = self.design_bytes()
        with self.assertRaises(ui.UIError) as cm:
            self.apply(ops)
        for f in fragments:
            self.assertIn(f, str(cm.exception))
        self.assertEqual(self.design_bytes(), before, "a rejected apply must not write design files")
        return str(cm.exception)


# ---------------------------------------------------------------------------
# 1. Ops
# ---------------------------------------------------------------------------


class SetOpTest(ProjectCase):
    def test_set_props_and_null_deletes(self):
        r = self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hello", "foreground": None}})
        n = self.node("home.hero.title")
        self.assertEqual(n["text"], "Hello")
        self.assertNotIn("foreground", n)
        self.assertEqual(r["changed"], ["home.hero.title"])
        self.assertEqual(r["files"], ["screens/home.json"])

    def test_set_type_change_with_compatible_props(self):
        self.apply({"op": "set", "id": "profile.identity", "props": {"type": "hstack", "align": "center"}})
        n = self.node("profile.identity")
        self.assertEqual((n["type"], n["align"]), ("hstack", "center"))
        self.assertEqual([c["id"] for c in n["children"]], ["profile.name", "profile.email"])

    def test_set_type_change_alone_maps_align_across_axes(self):
        # Every vstack in the template has align:"leading"; the documented
        # vstack->hstack change must not need a second round trip to fix align.
        self.apply({"op": "set", "id": "profile.identity", "props": {"type": "hstack"}})
        n = self.node("profile.identity")
        self.assertEqual((n["type"], n["align"], n["spacing"]), ("hstack", "top", "$xs"))
        self.apply({"op": "set", "id": "profile.identity", "props": {"type": "vstack"}})
        self.assertEqual(self.node("profile.identity")["align"], "leading")

    def test_set_type_change_drops_props_the_new_type_lacks(self):
        self.apply({"op": "set", "id": "explore.grid", "props": {"type": "vstack"}})
        n = self.node("explore.grid")
        self.assertEqual(n["type"], "vstack")
        self.assertNotIn("columns", n)
        self.assertEqual(n["spacing"], "$md")

    def test_set_screen_level_props(self):
        r = self.apply({"op": "set", "id": "home", "props": {"title": "Today", "titleDisplay": "inline"}})
        s = self.screen("home")
        self.assertEqual((s["title"], s["titleDisplay"]), ("Today", "inline"))
        self.assertEqual(r["changed"], ["home"])

    def test_set_refuses_to_clobber_toolbar_and_data(self):
        self.assertRejected({"op": "set", "id": "home", "props": {"toolbar": []}}, "replace the whole toolbar")
        self.assertRejected({"op": "set", "id": "home", "props": {"data": {}}}, "replace all demo data")
        self.assertRejected({"op": "set", "id": "home", "props": {"body": {}}}, "cannot set screen 'body'")

    def test_set_explicit_toolbar_replace(self):
        self.apply({"op": "set", "id": "home", "replaceToolbar": True,
                    "props": {"toolbar": [{"type": "button", "symbol": "gearshape", "action": {"sheet": "compose"}}]}})
        tb = self.screen("home")["toolbar"]
        self.assertEqual(len(tb), 1)
        self.assertEqual(tb[0]["id"], "home.gearshape")

    def test_set_rename_to_taken_id_fails(self):
        self.assertRejected({"op": "set", "id": "home.section", "props": {"id": "home.hero"}}, "already used")

    def test_set_needs_props(self):
        self.assertRejected({"op": "set", "id": "home.section"}, "needs \"props\"")


class NudgeOpTest(ProjectCase):
    def nudge(self, nid, prop, by=1):
        self.apply({"op": "nudge", "id": nid, "prop": prop, "by": by})
        return self.node(nid)

    def test_size_ladder_from_explicit_size(self):
        self.apply({"op": "set", "id": "home.section", "props": {"size": 20}})
        self.assertEqual(self.nudge("home.section", "size")["size"], 22)
        self.assertEqual(self.nudge("home.section", "size", -3)["size"], 15)
        self.assertEqual(self.nudge("home.section", "size", -10)["size"], 11)  # bottom of the scale

    def test_size_from_font_token(self):
        self.assertEqual(self.nudge("home.hero.title", "size")["size"], 24)  # $title = 22

    def test_size_from_style_font_token(self):
        self.assertEqual(self.nudge("home.hero.subtitle", "size")["size"], 15)  # style secondaryText -> $caption 13

    def test_size_from_text_style_name(self):
        self.apply({"op": "set", "id": "home.section", "props": {"font": "headline"}})
        self.assertEqual(self.nudge("home.section", "size")["size"], 20)

    def test_symbol_image_size(self):
        self.assertEqual(self.nudge("profile.avatar", "size")["size"], 56)

    def test_padding_token_ladder(self):
        self.assertEqual(self.nudge("home.hero", "padding")["padding"], "$lg")  # style card: $md
        self.assertEqual(self.nudge("home.hero", "padding", -2)["padding"], "$sm")
        self.assertEqual(self.nudge("compose.title", "padding")["padding"], "$xs")  # none yet -> smallest

    def test_spacing_token_ladder(self):
        self.assertEqual(self.nudge("home.scroll", "spacing")["spacing"], "$lg")
        self.assertEqual(self.nudge("home.scroll", "spacing", -2)["spacing"], "$sm")

    def test_radius_token_ladder(self):
        self.assertEqual(self.nudge("home.story.image", "radius")["radius"], "$lg")
        # past the largest step ($pill = 999 is not a step) it keeps growing as a number
        self.assertEqual(self.nudge("home.story.image", "radius")["radius"], 29)
        self.assertEqual(self.nudge("home.story.image", "radius", -1)["radius"], "$lg")

    def test_nudge_that_changes_nothing_is_rejected(self):
        self.apply({"op": "set", "id": "home.section", "props": {"size": 11, "weight": "black"}})
        for prop, by in (("size", -1), ("weight", 1)):
            with self.assertRaises(ui.UIError) as cm:
                self.apply({"op": "nudge", "id": "home.section", "prop": prop, "by": by})
            self.assertIn("use set", str(cm.exception))
        self.apply({"op": "set", "id": "home.hero.cta", "props": {"controlSize": "extraLarge"}})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.hero.cta", "prop": "size"})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.hero", "prop": "opacity"})  # already 1.0
        self.apply({"op": "set", "id": "home.scroll", "props": {"spacing": 0}})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.scroll", "prop": "spacing", "by": -1})
        self.assertEqual(self.nudge("home.scroll", "spacing")["spacing"], "$xs")

    def test_steps_past_the_scale_reverse_cleanly(self):
        self.apply({"op": "set", "id": "home.section", "props": {"size": 64}})
        self.assertEqual(self.nudge("home.section", "size", 3)["size"], 140)
        self.assertEqual(self.nudge("home.section", "size", -1)["size"], 108)
        self.apply({"op": "set", "id": "home.section", "props": {"size": 400}})
        self.assertEqual(self.nudge("home.section", "size", -1)["size"], 308)
        self.apply({"op": "set", "id": "home.story.image", "props": {"radius": 49}})
        self.assertEqual(self.nudge("home.story.image", "radius", -1)["radius"], 38)
        self.assertEqual(self.nudge("home.story.image", "radius", -2)["radius"], "$lg")

    def test_spacing_reaches_zero_then_stops(self):
        self.apply({"op": "set", "id": "home.scroll", "props": {"spacing": "$xs"}})
        self.assertEqual(self.nudge("home.scroll", "spacing", -3)["spacing"], 0)
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.scroll", "prop": "spacing", "by": -1})

    def test_more_spacing_from_the_default_gap_grows(self):
        self.apply({"op": "set", "id": "home.scroll", "props": {"spacing": None}})
        self.assertEqual(self.nudge("home.scroll", "spacing")["spacing"], "$md")  # default gap is ~8

    def test_less_padding_or_radius_where_there_is_none_is_rejected(self):
        for nid, prop in (("home.hero.title", "padding"), ("home.section", "radius")):
            with self.assertRaises(ui.UIError) as cm:
                self.apply({"op": "nudge", "id": nid, "prop": prop, "by": -1})
            self.assertIn("would change nothing", str(cm.exception))
            self.assertNotIn(prop, self.node(nid))

    def test_pill_radius_does_not_grow_forever(self):
        self.apply({"op": "set", "id": "home.hero.cta", "props": {"radius": "$pill"}})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.hero.cta", "prop": "radius"})
        self.apply({"op": "set", "id": "home.hero.cta", "props": {"radius": 400}})
        self.assertEqual(self.nudge("home.hero.cta", "radius")["radius"], "$pill")

    def test_radius_from_a_spacing_token_resolves_like_the_renderer(self):
        self.apply({"op": "set", "id": "home.story.image", "props": {"radius": "$xl"}})  # spacing.xl = 32
        self.assertEqual(self.nudge("home.story.image", "radius", -1)["radius"], 25)  # above the scale: /1.3
        self.assertEqual(self.nudge("home.story.image", "radius", -1)["radius"], "$lg")
        self.apply({"op": "set", "id": "home.story.image", "props": {"radius": "$xl"}})
        self.assertEqual(self.nudge("home.story.image", "radius")["radius"], 42)

    def test_nudge_steps_are_bounded(self):
        for by in (21, 3000, -10 ** 9):
            with self.assertRaises(ui.UIError) as cm:
                self.apply({"op": "nudge", "id": "home.section", "prop": "size", "by": by})
            self.assertIn("between -20 and 20", str(cm.exception))

    def test_font_token_chains_and_style_objects(self):
        self.apply({"op": "token", "path": "fonts.hero", "value": "$largeTitle"})
        self.apply({"op": "set", "id": "home.hero.title", "props": {"font": "$hero"}})
        self.assertEqual(ui.effective_text_size(ui.Design(self.project), self.node("home.hero.title")), 34)
        self.apply({"op": "set", "id": "home.hero.title", "props": {"font": {"style": "largeTitle", "weight": "bold"}}})
        self.assertEqual(ui.effective_text_size(ui.Design(self.project), self.node("home.hero.title")), 34)
        self.assertEqual(self.nudge("home.hero.title", "size", -1)["size"], 28)

    def test_noop_follows_font_weight_and_style_control_size(self):
        self.apply({"op": "token", "path": "fonts.title.weight", "value": "black"})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.hero.title", "prop": "weight"})
        self.apply({"op": "token", "path": "styles.big", "value": {"controlSize": "extraLarge"}})
        self.apply({"op": "set", "id": "home.hero.cta", "props": {"style": "big", "controlSize": None}})
        with self.assertRaises(ui.UIError):
            self.apply({"op": "nudge", "id": "home.hero.cta", "prop": "size"})
        self.assertEqual(self.nudge("home.hero.cta", "size", -1)["controlSize"], "large")

    def test_small_frames_always_move(self):
        self.apply({"op": "set", "id": "home.hero", "props": {"height": 3}})
        self.assertEqual(self.nudge("home.hero", "height")["height"], 4)
        self.assertEqual(self.nudge("home.hero", "height", -2)["height"], 2)

    def test_size_keeps_growing_past_the_type_scale(self):
        self.apply({"op": "set", "id": "home.section", "props": {"size": 64}})
        self.assertEqual(self.nudge("home.section", "size")["size"], 83)

    def test_weight(self):
        self.assertEqual(self.nudge("home.section", "weight")["weight"], "bold")  # $headline is semibold
        self.assertEqual(self.nudge("profile.email", "weight")["weight"], "semibold")  # $caption is medium
        self.assertEqual(self.nudge("compose.hint", "weight", -1)["weight"], "regular")

    def test_weight_bolder_never_lightens_a_bold_font_token(self):
        self.assertEqual(self.nudge("home.hero.title", "weight")["weight"], "heavy")  # $title is bold

    def test_weight_without_font_starts_at_regular(self):
        self.assertEqual(self.nudge("profile.help", "weight")["weight"], "medium")

    def test_opacity(self):
        self.assertEqual(self.nudge("home.section", "opacity", -1)["opacity"], 0.9)
        self.assertEqual(self.nudge("home.section", "opacity", -20)["opacity"], 0.0)

    def test_button_size_is_control_size(self):
        n = self.nudge("home.hero.cta", "size")
        self.assertEqual(n["controlSize"], "extraLarge")
        self.assertNotIn("size", n)
        self.assertEqual(self.nudge("story.save", "size")["controlSize"], "large")  # default regular
        self.assertEqual(self.nudge("story.save", "size", -9)["controlSize"], "mini")

    def test_raster_image_scales_frame(self):
        n = self.nudge("home.story.image", "size")
        self.assertEqual((n["width"], n["height"]), (90, 90))
        n = self.nudge("story.image", "size")  # only a height
        self.assertEqual(n["height"], 325)
        self.assertEqual(n["maxWidth"], "infinity")

    def test_width(self):
        self.assertEqual(self.nudge("home.story.image", "width")["width"], 83)

    def test_bad_nudges(self):
        self.assertRejected({"op": "nudge", "id": "home.section", "prop": "color"}, "prop must be one of")
        self.assertRejected({"op": "nudge", "id": "home.section", "prop": "size", "by": 0}, "non-zero integer")
        self.assertRejected({"op": "nudge", "id": "home.section", "prop": "width"}, "no numeric value")
        self.assertRejected({"op": "nudge", "id": "story.content", "prop": "padding"}, "per-edge")


class InsertOpTest(ProjectCase):
    TEXT = {"type": "text", "text": "New line"}

    def test_insert_after_and_before(self):
        self.apply({"op": "insert", "after": "home.hero.title", "node": self.TEXT})
        self.assertEqual(self.child_ids("home.hero")[:2], ["home.hero.title", "home.new-line"])
        self.apply({"op": "insert", "before": "home.hero.title", "node": {"type": "divider"}})
        self.assertEqual(self.child_ids("home.hero")[0], "home.divider")

    def test_insert_parent_and_index(self):
        r = self.apply({"op": "insert", "parent": "home.hero", "index": 0, "node": self.TEXT})
        self.assertEqual(self.child_ids("home.hero")[0], "home.new-line")
        self.assertEqual(r["changed"], ["home.new-line"])
        self.apply({"op": "insert", "parent": "home.hero", "node": {"type": "spacer"}})
        self.assertEqual(self.child_ids("home.hero")[-1], "home.spacer")
        self.apply({"op": "insert", "parent": "home.hero", "index": -1, "node": {"type": "divider"}})
        self.assertEqual(self.child_ids("home.hero")[-1], "home.divider")

    def test_insert_into_toolbar(self):
        self.apply({"op": "insert", "parent": "home.toolbar", "node": {"type": "button", "symbol": "gearshape",
                                                                        "action": {"sheet": "compose"}}})
        tb = self.screen("home")["toolbar"]
        self.assertEqual([t["id"] for t in tb], ["home.compose", "home.gearshape"])
        self.apply({"op": "insert", "parent": "profile.toolbar", "index": 0, "node": {"type": "button", "text": "Edit"}})
        self.assertEqual(self.screen("profile")["toolbar"][0]["id"], "profile.edit")

    def test_insert_parent_is_screen_id(self):
        self.apply({"op": "insert", "parent": "compose", "node": self.TEXT})
        self.assertEqual(self.child_ids("compose.body")[-1], "compose.new-line")

    def test_insert_into_leaf_or_repeat_fails(self):
        self.assertRejected({"op": "insert", "parent": "home.section", "node": self.TEXT}, "cannot have children")
        self.assertRejected({"op": "insert", "parent": "home.stories", "node": self.TEXT}, "template ('home.story')")
        self.assertRejected({"op": "insert", "after": "home.story", "node": self.TEXT}, "repeat template")
        self.assertRejected({"op": "insert", "node": self.TEXT}, "needs \"after\"")


class TreeOpsTest(ProjectCase):
    def test_remove_by_id_and_ids(self):
        r = self.apply({"op": "remove", "id": "home.section"})
        self.assertNotIn("home.section", self.design().all_ids())
        self.assertEqual(r["changed"], ["home.scroll"])
        self.apply({"op": "remove", "ids": ["home.hero.title", "home.hero.subtitle"]})
        self.assertEqual(self.child_ids("home.hero"), ["home.hero.cta"])

    def test_remove_template_or_body_fails(self):
        self.assertRejected({"op": "remove", "id": "home.story"}, "repeat template")
        self.assertRejected({"op": "remove", "id": "home.scroll"}, "screen body")
        self.assertRejected({"op": "remove", "id": "home.nope"}, "no element with id 'home.nope'")

    def test_move(self):
        self.apply({"op": "move", "id": "home.section", "before": "home.hero"})
        self.assertEqual(self.child_ids("home.scroll"), ["home.section", "home.hero", "home.stories"])
        self.apply({"op": "move", "id": "home.hero.subtitle", "parent": "home.scroll", "index": 0})
        self.assertEqual(self.child_ids("home.scroll")[0], "home.hero.subtitle")
        self.assertEqual(self.child_ids("home.hero"), ["home.hero.title", "home.hero.cta"])

    def test_move_into_own_descendant_fails(self):
        self.assertRejected({"op": "move", "id": "home.hero", "parent": "home.hero.cta"}, "inside itself")
        self.assertRejected({"op": "move", "id": "home.hero", "after": "home.hero.title"}, "inside itself")
        self.assertRejected({"op": "move", "id": "home.hero", "after": "home.hero"}, "relative to itself")

    def test_move_to_missing_target_restores_node(self):
        self.assertRejected({"op": "move", "id": "home.section", "after": "home.nope"}, "no element")

    def test_replace_keeps_id(self):
        r = self.apply({"op": "replace", "id": "home.hero.title",
                        "node": {"type": "label", "text": "Hi", "symbol": "sun.max"}})
        n = self.node("home.hero.title")
        self.assertEqual((n["type"], n["text"], n["symbol"]), ("label", "Hi", "sun.max"))
        self.assertEqual(self.child_ids("home.hero")[0], "home.hero.title")
        self.assertEqual(r["changed"], ["home.hero.title"])

    def test_wrap_preserves_document_order(self):
        self.apply({"op": "wrap", "ids": ["home.hero.subtitle", "home.hero.title"],
                    "node": {"type": "hstack", "spacing": "$sm"}})
        kids = self.node("home.hero")["children"]
        self.assertEqual(kids[0]["id"], "home.hstack")
        self.assertEqual([c["id"] for c in kids[0]["children"]], ["home.hero.title", "home.hero.subtitle"])
        self.assertEqual([c["id"] for c in kids], ["home.hstack", "home.hero.cta"])

    def test_wrap_default_container_and_alias(self):
        self.apply({"op": "wrap", "ids": ["home.section"]})
        self.assertEqual(self.node("home.hstack")["children"][0]["id"], "home.section")
        self.apply({"op": "wrap", "ids": ["profile.name", "profile.email"], "node": {"type": "VStack"}})
        self.assertEqual(self.node("profile.vstack")["type"], "vstack")

    def test_wrap_non_siblings_fails(self):
        self.assertRejected({"op": "wrap", "ids": ["home.hero.title", "home.section"]}, "siblings")
        self.assertRejected({"op": "wrap", "ids": ["home.section"], "node": {"type": "text"}}, "container")

    def test_unwrap(self):
        self.apply({"op": "unwrap", "id": "home.story.text"})
        self.assertEqual(self.child_ids("home.story"),
                         ["home.story.image", "home.story.title", "home.story.meta", "home.story.spacer",
                          "home.story.chevron"])
        self.assertRejected({"op": "unwrap", "id": "home.section"}, "no children")


class DataOpsTest(ProjectCase):
    def test_token_set_create_and_delete(self):
        r = self.apply({"op": "token", "path": "tokens.colors.brand", "value": "#0EA5E9"})
        self.assertEqual(self.tokens()["colors"]["brand"], "#0EA5E9")
        self.assertEqual(r["changed"], ["tokens.colors.brand"])
        self.apply({"op": "token", "path": "styles.pill.radius", "value": "$pill"})
        self.assertEqual(self.tokens()["styles"]["pill"], {"radius": "$pill"})
        self.apply({"op": "token", "path": "colors.accent", "value": None})
        self.assertNotIn("accent", self.tokens()["colors"])
        self.apply({"op": "token", "path": "nothing.here", "value": None})  # deleting nothing is a no-op
        self.assertNotIn("nothing", self.tokens())

    def test_token_delete_in_use_is_rejected(self):
        self.assertRejected({"op": "token", "path": "colors.brand", "value": None}, "no color token '$brand'")

    def test_data(self):
        self.apply({"op": "data", "screen": "home", "path": "stories.1.title", "value": "Field notes"})
        self.assertEqual(self.screen("home")["data"]["stories"][1]["title"], "Field notes")
        story = {"title": "New", "author": "Me", "minutes": "1", "image": "https://picsum.photos/1"}
        r = self.apply({"op": "data", "screen": "home", "path": "data.stories.-", "value": story})
        self.assertEqual(self.screen("home")["data"]["stories"][-1], story)
        self.assertEqual(len(self.screen("home")["data"]["stories"]), 4)
        self.assertEqual(r["changed"], ["home"])
        self.apply({"op": "data", "screen": "home", "path": "stories.0", "value": None})
        self.assertEqual(len(self.screen("home")["data"]["stories"]), 3)

    def test_data_append_creates_a_list(self):
        self.apply({"op": "data", "screen": "profile", "path": "tags.-", "value": "swift"})
        self.assertEqual(self.screen("profile")["data"], {"tags": ["swift"]})

    def test_data_errors(self):
        self.assertRejected({"op": "data", "screen": "nope", "path": "x", "value": 1}, "needs \"screen\"")
        self.assertRejected({"op": "data", "screen": "home", "path": "stories.9.title", "value": "x"}, "out of range")

    def test_screen_create_with_auto_ids(self):
        spec = {"title": "Settings", "body": {"type": "list", "children": [
            {"type": "section", "header": "General", "children": [
                {"type": "toggle", "text": "Notifications", "bind": "notify"}]}]}}
        r = self.apply([{"op": "screen", "id": "settings", "spec": spec},
                        {"op": "insert", "parent": "home.toolbar",
                         "node": {"type": "button", "symbol": "gearshape", "action": {"sheet": "settings"}}}])
        s = self.screen("settings")
        self.assertEqual(s["id"], "settings")
        self.assertEqual(s["body"]["id"], "settings.list")
        self.assertEqual(s["body"]["children"][0]["id"], "settings.general")
        self.assertEqual(s["body"]["children"][0]["children"][0]["id"], "settings.notifications")
        self.assertIn("screens/settings.json", r["files"])
        self.assertIn("new screen settings", r["summary"])

    def test_screen_replace_reuses_ids(self):
        self.apply({"op": "screen", "id": "compose", "spec": {"title": "Write", "body": {
            "type": "vstack", "children": [{"type": "textfield", "placeholder": "Title", "bind": "t"}]}}})
        s = self.screen("compose")
        self.assertEqual(s["title"], "Write")
        self.assertNotIn("toolbar", s)
        self.assertEqual(s["body"]["id"], "compose.vstack")
        self.assertEqual(s["body"]["children"][0]["id"], "compose.title")  # freed by the replace

    def test_screen_without_spec_does_not_delete(self):
        self.assertRejected({"op": "screen", "id": "compose"}, "spec")
        self.assertTrue((self.root / "design" / "screens" / "compose.json").exists())

    def test_screen_delete_with_explicit_null(self):
        self.apply({"op": "set", "id": "home.compose", "props": {"action": {"dismiss": True}}})
        self.apply({"op": "screen", "id": "compose", "spec": None})
        self.assertFalse((self.root / "design" / "screens" / "compose.json").exists())

    def test_screen_delete_still_referenced_is_rejected(self):
        self.assertRejected({"op": "screen", "id": "compose", "spec": None}, "sheet: screen 'compose' does not exist")

    def test_path(self):
        self.apply({"op": "path", "file": "app", "path": "colorScheme", "value": "dark"})
        self.assertEqual(json.loads((self.root / "design" / "app.json").read_text())["colorScheme"], "dark")
        self.apply({"op": "path", "file": "screens/home", "path": "toolbar.0.symbol", "value": "plus"})
        self.assertEqual(self.screen("home")["toolbar"][0]["symbol"], "plus")
        self.apply({"op": "path", "file": "tokens.json", "path": "spacing.xxl", "value": 48})
        self.assertEqual(self.tokens()["spacing"]["xxl"], 48)

    def test_path_errors(self):
        self.assertRejected({"op": "path", "file": "app", "value": 1}, "path")
        self.assertRejected({"op": "path", "file": "screens/nope", "path": "x", "value": 1}, "no screen 'nope'")
        self.assertRejected({"op": "path", "path": "x", "value": 1}, "needs \"file\"")

    def test_unknown_op(self):
        with self.assertRaises(ui.UIError) as cm:
            ui.parse_ops('{"op":"sett","id":"x"}')
        self.assertIn("Did you mean set", str(cm.exception))


class AtomicityTest(ProjectCase):
    def test_last_op_failing_writes_nothing(self):
        before = self.design_bytes()
        ops = [{"op": "set", "id": "home.hero.title", "props": {"text": "Changed"}},
               {"op": "token", "path": "colors.brand", "value": "#000000"},
               {"op": "screen", "id": "settings", "spec": {"title": "S", "body": {"type": "vstack"}}},
               {"op": "remove", "id": "home.does-not-exist"}]
        with self.assertRaises(ui.UIError) as cm:
            self.apply(ops)
        self.assertIn("op #4 (remove)", str(cm.exception))
        self.assertEqual(self.design_bytes(), before)
        self.assertFalse((self.live.dir / "rev").exists())
        self.assertFalse((self.live.dir / "history").exists())
        self.assertFalse((self.live.dir / "history.jsonl").exists())

    def test_validation_failure_writes_nothing(self):
        msg = self.assertRejected([{"op": "set", "id": "home.hero.title", "props": {"text": "ok"}},
                                   {"op": "set", "id": "home.section", "props": {"foreground": "$nope"}}],
                                  "rejected, nothing was written", "no color token '$nope'")
        self.assertIn("home.section", msg)
        self.assertFalse((self.live.dir / "rev").exists())


# ---------------------------------------------------------------------------
# 2. Auto-fixes
# ---------------------------------------------------------------------------


class FixerTest(ProjectCase):
    def insert(self, node, after="home.section"):
        r = self.apply({"op": "insert", "after": after, "node": node})
        return self.node(r["changed"][0]), r

    def test_prop_aliases(self):
        n, _ = self.insert({"type": "text", "title": "Hi", "color": "blue", "fontSize": "20", "cornerRadius": 4,
                            "fontWeight": "bold", "backgroundColor": "red", "lineLimit": 2})
        self.assertEqual({k: n[k] for k in ("text", "foreground", "size", "radius", "weight", "background", "lines")},
                         {"text": "Hi", "foreground": "blue", "size": 20, "radius": 4, "weight": "bold",
                          "background": "red", "lines": 2})
        for k in ("title", "color", "fontSize", "cornerRadius", "fontWeight", "backgroundColor", "lineLimit"):
            self.assertNotIn(k, n)

    def test_type_aliased_props(self):
        n, _ = self.insert({"type": "Circle", "color": "red", "width": 10, "height": 10})
        self.assertEqual((n["type"], n["shape"], n["fill"]), ("shape", "circle", "red"))
        n, _ = self.insert({"type": "row", "gap": 8, "children": [{"type": "Text", "label": "A"}]})
        self.assertEqual((n["type"], n["spacing"]), ("hstack", 8))
        self.assertEqual(n["children"][0]["text"], "A")
        n, _ = self.insert({"type": "VStack", "alignment": "left"})
        self.assertEqual((n["type"], n["align"]), ("vstack", "leading"))
        n, _ = self.insert({"type": "Button", "title": "Go", "icon": "star", "onPress": {"dismiss": True}})
        self.assertEqual((n["type"], n["text"], n["symbol"], n["action"]), ("button", "Go", "star", {"dismiss": True}))

    def test_section_title_is_header(self):
        n, _ = self.insert({"type": "section", "title": "More", "children": []}, after="profile.more")
        self.assertEqual(n["header"], "More")

    def test_button_style_primary_is_variant(self):
        n, r = self.insert({"type": "button", "text": "Go", "style": "primary"})
        self.assertEqual(n["variant"], "primary")
        self.assertNotIn("style", n)
        self.assertIn("home.go: style→variant", r["notes"])
        n, _ = self.insert({"type": "vstack", "style": "card"})  # a real style bundle stays
        self.assertEqual(n["style"], "card")

    def test_bare_token_names(self):
        n, _ = self.insert({"type": "text", "text": "x", "foreground": "brand", "background": "surface",
                            "padding": "md", "radius": "pill", "tint": "blue"})
        self.assertEqual((n["foreground"], n["background"], n["padding"], n["radius"], n["tint"]),
                         ("$brand", "$surface", "$md", "$pill", "blue"))

    def test_css_colors(self):
        n, _ = self.insert({"type": "text", "text": "x", "foreground": "coral", "background": "Navy"})
        self.assertEqual((n["foreground"], n["background"]), ("#FF7F50", "#000080"))

    def test_directions(self):
        n, _ = self.insert({"type": "text", "text": "x", "textAlign": "left", "maxWidth": "infinity",
                            "frameAlign": "top-right"})
        self.assertEqual((n["textAlign"], n["frameAlign"]), ("leading", "topTrailing"))

    def test_numeric_strings(self):
        n, _ = self.insert({"type": "vstack", "width": "120", "opacity": "0.5", "spacing": "8", "padding": "12.5"})
        self.assertEqual((n["width"], n["opacity"], n["spacing"], n["padding"]), (120, 0.5, 8, 12.5))
        n, _ = self.insert({"type": "grid", "columns": "3", "children": []})
        self.assertEqual(n["columns"], 3)
        n, _ = self.insert({"type": "text", "text": "x", "lines": "2"})
        self.assertEqual(n["lines"], 2)

    def test_cookbook_center_text_keeps_infinity(self):
        r = self.apply({"op": "set", "id": "home.hero.subtitle",
                        "props": {"maxWidth": "infinity", "frameAlign": "center", "textAlign": "center"}})
        n = self.node("home.hero.subtitle")
        self.assertEqual((n["maxWidth"], n["frameAlign"], n["textAlign"]), ("infinity", "center", "center"))
        self.assertEqual(r["notes"], [])
        self.assertNotIn("Infinity", (self.root / "design" / "screens" / "home.json").read_text())

    def test_button_on_tap_becomes_action(self):
        # Swift runs only "action" on a button (its onTap is ignored there)
        r = self.apply({"op": "set", "id": "story.save", "props": {"onTap": {"dismiss": True}}})
        n = self.node("story.save")
        self.assertEqual(n["action"], {"dismiss": True})
        self.assertNotIn("onTap", n)
        self.assertIn("story.save: onTap→action", r["notes"])
        n, _ = self.insert({"type": "text", "text": "x", "action": {"dismiss": True}})
        self.assertEqual(n["onTap"], {"dismiss": True})  # and the reverse on non-buttons

    def test_enum_case(self):
        n, _ = self.insert({"type": "button", "text": "Go", "variant": "Primary", "controlSize": "LARGE"})
        self.assertEqual((n["variant"], n["controlSize"]), ("primary", "large"))

    def test_opacity_percent(self):
        n, _ = self.insert({"type": "text", "text": "x", "opacity": 50})
        self.assertEqual(n["opacity"], 0.5)

    def test_auto_ids_are_screen_scoped_and_unique(self):
        a, _ = self.insert({"type": "text", "text": "Hello there, friend"})
        b, _ = self.insert({"type": "text", "text": "Hello there"})
        c, _ = self.insert({"type": "image", "symbol": "star.fill"}, after="profile.name")
        d, _ = self.insert({"type": "vstack"}, after="profile.name")
        self.assertEqual((a["id"], b["id"], c["id"], d["id"]),
                         ("home.hello-there", "home.hello-there2", "profile.star-fill", "profile.vstack"))
        e, _ = self.insert({"type": "text", "text": "{{name}}"}, after="explore.tile.name")
        self.assertEqual(e["id"], "explore.text")  # templates don't leak into ids

    def test_auto_id_never_equals_a_screen_id(self):
        r = self.apply({"op": "screen", "id": "x", "spec": {"title": "X", "body": {"type": "text", "text": "x"}}})
        self.assertEqual(self.screen("x")["body"]["id"], "x.x")
        self.assertIn("new screen x", r["summary"])

    def test_notes_use_final_node_id(self):
        _, r = self.insert({"type": "VStack", "children": [{"type": "Text", "title": "Hi there", "color": "coral"}]})
        self.assertIn("home.vstack: type VStack→vstack", r["notes"])
        self.assertIn("home.hi-there: type Text→text", r["notes"])
        self.assertIn("home.hi-there: title→text", r["notes"])
        self.assertIn('home.hi-there: foreground "coral"→"#FF7F50"', r["notes"])
        r = self.apply({"op": "set", "id": "home.section", "props": {"cornerRadius": 4, "opacity": 50}})
        self.assertIn("home.section: cornerRadius→radius", r["notes"])
        self.assertIn("home.section: opacity 50→0.5", r["notes"])
        self.assertEqual(self.node("home.section")["radius"], 4)

    def test_node_order_puts_id_after_type(self):
        n, _ = self.insert({"text": "x", "type": "text"})
        self.assertEqual(list(n)[:3], ["type", "id", "text"])


# ---------------------------------------------------------------------------
# 3. Validator
# ---------------------------------------------------------------------------


class ValidatorTest(ProjectCase):
    def run_validator(self, mutate=None):
        d = self.design()
        if mutate:
            mutate(d)
        return ui.Validator(d).run()

    def assertError(self, mutate, *fragments):
        errors = self.run_validator(mutate).errors
        hits = [e for e in errors if all(f in e for f in fragments)]
        self.assertTrue(hits, f"no error with {fragments!r} in {errors!r}")

    def set_prop(self, nid, key, value):
        def mutate(d):
            d.find(nid).node[key] = value
        return mutate

    def test_template_design_is_valid(self):
        v = self.run_validator()
        self.assertEqual(v.errors, [])
        self.assertEqual(v.warnings, [])

    def test_unknown_type_with_suggestion(self):
        self.assertError(self.set_prop("home.section", "type", "txt"), "home.section", 'unknown type "txt"',
                         "Did you mean text?")

    def test_unknown_prop_with_alias_hint(self):
        self.assertError(self.set_prop("home.section", "cornerRadius", 4),
                         '"cornerRadius" is not a text property. Use "radius".')
        self.assertError(self.set_prop("home.section", "paddng", 4), "Did you mean padding")

    def test_missing_token_did_you_mean(self):
        self.assertError(self.set_prop("home.section", "foreground", "$brnd"), "no color token '$brnd'",
                         "Did you mean brand", 'Add it with {"op":"token","path":"colors.brnd"')
        self.assertError(self.set_prop("home.section", "padding", "$mdd"), "no spacing/radius token '$mdd'",
                         "Did you mean md")
        self.assertError(self.set_prop("home.section", "font", "$titel"), "no font token '$titel'",
                         "Did you mean title")
        self.assertError(self.set_prop("home.hero", "style", "cards"), "style 'cards' is not in tokens.styles",
                         "Did you mean card")

    def test_bad_colors(self):
        self.assertError(self.set_prop("home.section", "foreground", "#12345"), "is not #RGB, #RRGGBB or #RRGGBBAA")
        self.assertError(self.set_prop("home.section", "foreground", "blurple"), "unknown color 'blurple'")
        self.assertError(self.set_prop("home.section", "background", {"gradient": ["red"]}), "2+ colors")
        self.assertError(self.set_prop("home.section", "border", {"color": "nope", "width": 1}), "border.color")

    def test_navigate_to_missing_screen(self):
        self.assertError(self.set_prop("home.hero.cta", "action", {"navigate": "settings"}),
                         "navigate: screen 'settings' does not exist", "Create it with a \"screen\" op")

    def test_tab_missing(self):
        self.assertError(self.set_prop("home.hero.cta", "action", {"tab": "feed"}), "tab 'feed' does not exist")
        self.assertError(self.set_prop("home.hero.cta", "action", {"tab": "explore", "sheet": "compose"}),
                         "exactly one of")

    def test_app_tab_screen_missing(self):
        def mutate(d):
            d.app["tabs"][1]["screen"] = "explorer"
        self.assertError(mutate, "app.json tabs[1]", "screen 'explorer' does not exist", "Did you mean explore")

    def test_duplicate_id(self):
        self.assertError(self.set_prop("home.section", "id", "profile.name"), "profile.name",
                         "duplicate id (also in screens/")

    def test_example_ids_rejected(self):
        self.assertError(self.set_prop("home.section", "id", "ex.title"), "ex.title", "reference examples")

    def test_node_id_equal_to_screen_id(self):
        self.assertError(self.set_prop("home.section", "id", "story"), "a node id equals a screen id")

    def test_columns_out_of_range(self):
        self.assertError(self.set_prop("explore.grid", "columns", 13), "columns must be 1–12")
        self.assertError(self.set_prop("explore.grid", "columns", 0), "columns must be 1–12")
        self.assertError(self.set_prop("explore.grid", "columns", 2.5), "columns must be an integer")

    def test_lines_below_one(self):
        self.assertError(self.set_prop("home.story.title", "lines", 0), "lines must be 1 or more")

    def test_repeat_limits(self):
        self.assertError(self.set_prop("home.stories", "items", 501), "repeat renders at most 500 items")
        self.assertError(self.set_prop("home.stories", "items", list(range(501))), "at most 500 items")
        self.assertEqual(self.run_validator(self.set_prop("home.stories", "items", 500)).errors, [])

    def test_opacity_range(self):
        self.assertError(self.set_prop("home.section", "opacity", 1.5), "opacity must be between 0 and 1")
        self.assertError(self.set_prop("home.section", "opacity", -0.1), "opacity must be between 0 and 1")

    def test_items_path_missing_in_data(self):
        self.assertError(self.set_prop("home.stories", "items", "data.storys"),
                         "items 'data.storys': screens/home.json has no data.storys", "Did you mean stories?")

    def test_structure_errors(self):
        self.assertError(self.set_prop("home.section", "children", []), "a text cannot have children")
        self.assertError(lambda d: d.find("home.stories").node.pop("template"), "repeat needs a \"template\"")
        self.assertError(lambda d: d.find("home.story.chevron").node.pop("symbol"), "image needs")
        self.assertError(self.set_prop("home.section", "align", "leading"), '"align" is not a text property')
        self.assertError(self.set_prop("home.story", "align", "leading"), "align 'leading' not valid for hstack")
        self.assertError(self.set_prop("home.section", "visibleIf", None), '"visibleIf" is null')

    def test_load_errors(self):
        (self.root / "design" / "screens" / "story.json").write_text("{ nope", encoding="utf-8")
        v = self.run_validator()
        self.assertTrue(any("screens/story.json: invalid JSON" in e for e in v.errors), v.errors)
        before = self.design_bytes()
        with self.assertRaises(ui.UIError) as cm:
            self.apply({"op": "set", "id": "home.section", "props": {"text": "x"}})
        self.assertIn("design files are broken", str(cm.exception))
        self.assertEqual(self.design_bytes(), before)

    def test_hidden_screen_files_are_ignored(self):
        (self.root / "design" / "screens" / ".draft.json").write_text("{ half-written", encoding="utf-8")
        self.assertEqual(self.run_validator().errors, [])
        self.assertNotIn("", self.design().screens)

    def test_warnings(self):
        v = self.run_validator(self.set_prop("compose.hint", "frameAlign", "center"))
        self.assertEqual(v.errors, [])
        self.assertTrue(any("compose.hint: frameAlign does nothing" in w for w in v.warnings), v.warnings)
        r = self.apply({"op": "set", "id": "compose.hint", "props": {"frameAlign": "center"}})
        self.assertTrue(any("frameAlign does nothing" in w for w in r["warnings"]))
        v = self.run_validator(self.set_prop("compose.hint", "textAlign", "center"))
        self.assertTrue(any("textAlign only affects multi-line text" in w for w in v.warnings), v.warnings)

    def test_dynamic_values_are_not_checked(self):
        self.assertEqual(self.run_validator(self.set_prop("home.section", "foreground", "{{color}}")).errors, [])


# ---------------------------------------------------------------------------
# 4. Undo
# ---------------------------------------------------------------------------


class UndoTest(ProjectCase):
    def undo(self, steps=1):
        with captured() as (out, _):
            code = ui.cmd_undo(self.project, argparse.Namespace(steps=steps))
        return code, out.getvalue()

    def test_snapshot_restore_round_trip(self):
        d = self.root / "design"
        texts = {str(p.relative_to(d)): p.read_text(encoding="utf-8") for p in d.rglob("*.json")}
        before = self.design_bytes()
        self.live.snapshot(texts, "label")
        (d / "app.json").write_text("{}", encoding="utf-8")
        (d / "screens" / "extra.json").write_text("{}", encoding="utf-8")
        snap = self.live.latest_snapshot()
        restored = {str(p.relative_to(snap)): p.read_text(encoding="utf-8") for p in snap.rglob("*.json")}
        ui.restore_texts(self.project, restored)
        self.assertEqual(self.design_bytes(), before)

    def test_apply_then_undo_is_byte_identical(self):
        before = self.design_bytes()
        self.apply([{"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}},
                    {"op": "token", "path": "colors.brand", "value": "#000000"},
                    {"op": "path", "file": "app", "path": "colorScheme", "value": "dark"}])
        self.assertNotEqual(self.design_bytes(), before)
        code, out = self.undo()
        self.assertEqual(code, 0)
        self.assertIn("ok  undid: set home.hero.title", out)
        self.assertEqual(self.design_bytes(), before)

    def test_undo_steps(self):
        before = self.design_bytes()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "One"}})
        middle = self.design_bytes()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Two"}})
        self.undo()
        self.assertEqual(self.design_bytes(), middle)
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Three"}})
        self.undo(2)
        self.assertEqual(self.design_bytes(), before)

    def test_undo_after_creating_screen_removes_file(self):
        self.apply({"op": "screen", "id": "settings", "spec": {"title": "S", "body": {"type": "vstack"}}})
        path = self.root / "design" / "screens" / "settings.json"
        self.assertTrue(path.exists())
        self.undo()
        self.assertFalse(path.exists())

    def test_undo_after_deleting_screen_restores_file(self):
        before = self.design_bytes()
        self.apply([{"op": "set", "id": "home.compose", "props": {"action": {"dismiss": True}}},
                    {"op": "screen", "id": "compose", "spec": None}])
        self.undo()
        self.assertEqual(self.design_bytes(), before)

    def test_undo_with_nothing(self):
        code, out = self.undo()
        self.assertEqual(code, 1)
        self.assertIn("nothing to undo", out)
        with captured():
            self.assertEqual(ui.main(["undo"]), 1)

    def test_noop_apply_takes_no_snapshot(self):
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Good morning"}})
        self.assertIsNone(self.live.latest_snapshot())


# ---------------------------------------------------------------------------
# 5. Live channel
# ---------------------------------------------------------------------------


class LiveChannelTest(ProjectCase):
    def write_status(self, **status):
        self.live.dir.mkdir(parents=True, exist_ok=True)
        ui.write_atomic(self.live.dir / "status.json", json.dumps(status))

    def test_rev_written_after_design_files(self):
        writes = []
        real = ui.write_atomic

        def recorder(path, text):
            writes.append(Path(path))
            real(path, text)

        with mock.patch.object(ui, "write_atomic", recorder):
            r = self.apply([{"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}},
                            {"op": "token", "path": "colors.brand", "value": "#111111"}])
        design = self.root / "design"
        design_writes = [i for i, p in enumerate(writes) if design in p.parents]
        rev_writes = [i for i, p in enumerate(writes) if p == self.live.dir / "rev"]
        self.assertEqual(len(design_writes), 2)
        self.assertEqual(len(rev_writes), 1)
        self.assertGreater(rev_writes[0], max(design_writes))
        rev = json.loads((self.live.dir / "rev").read_text())
        self.assertEqual(rev["rev"], 1)
        self.assertEqual(rev["changed"], ["home.hero.title", "tokens.colors.brand"])
        self.assertEqual(rev["changed"], r["changed"])
        self.assertFalse(rev["quiet"])
        self.apply({"op": "nudge", "id": "home.section", "prop": "size"})
        self.assertEqual(json.loads((self.live.dir / "rev").read_text())["rev"], 2)

    def test_wait_rev_without_status(self):
        self.assertEqual(self.live.wait_rev(1), (None, 0.0))

    def test_wait_rev_confirmed_by_app(self):
        self.write_status(pid=os.getpid(), rev=0, screen="home")

        def app_renders():
            time.sleep(0.1)
            ui.write_atomic(self.live.dir / "status.json", json.dumps({"rev": 3}))

        t = threading.Thread(target=app_renders)
        t.start()
        try:
            status, secs = self.live.wait_rev(3, timeout=3)
        finally:
            t.join()
        self.assertEqual(status, {"rev": 3})
        self.assertGreaterEqual(secs, 0.05)
        self.assertLess(secs, 2.0)

    def test_wait_rev_dead_app(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()  # reaped: the pid no longer exists
        self.write_status(pid=proc.pid, rev=0)
        self.assertEqual(self.live.wait_rev(1), (None, 0.0))
        self.assertFalse(self.live.app_alive())

    def test_wait_rev_timeout(self):
        self.write_status(pid=os.getpid(), rev=1)
        status, secs = self.live.wait_rev(2, timeout=0.2)
        self.assertIs(status, False)
        self.assertGreaterEqual(secs, 0.2)

    def test_apply_reports_confirmed_render(self):
        self.write_status(pid=os.getpid(), rev=0, screen="home")

        def app():
            wait_until(lambda: (self.live.dir / "rev").exists())
            ui.write_atomic(self.live.dir / "status.json", json.dumps({"pid": os.getpid(), "rev": 1, "screen": "home"}))

        t = threading.Thread(target=app)
        t.start()
        try:
            with captured() as (out, _):
                r = self.apply({"op": "set", "id": "home.section", "props": {"text": "News"}}, wait=True, quiet=False)
        finally:
            t.join()
        self.assertEqual(r["status"]["rev"], 1)
        self.assertIn("live on 'home'", out.getvalue())
        last = json.loads((self.live.dir / "last-apply.json").read_text())
        self.assertEqual((last["app"], last["screen"]), ("live", "home"))

    def test_project_lock_serializes_threads(self):
        events = []

        def worker(name):
            with ui.project_lock(self.project):
                events.append(name + ":in")
                time.sleep(0.15)
                events.append(name + ":out")

        a = threading.Thread(target=worker, args=("a",))
        b = threading.Thread(target=worker, args=("b",))
        a.start()
        self.assertTrue(wait_until(lambda: "a:in" in events))
        b.start()
        a.join()
        b.join()
        self.assertEqual(events, ["a:in", "a:out", "b:in", "b:out"])


# ---------------------------------------------------------------------------
# 6. extract_json
# ---------------------------------------------------------------------------


class ExtractJsonTest(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(ui.extract_json('Here:\n```json\n{"ops": [1]}\n```\nthanks'), {"ops": [1]})
        self.assertEqual(ui.extract_json('```\n[{"op": "remove", "id": "a"}]\n```'), [{"op": "remove", "id": "a"}])

    def test_prose_around_object(self):
        self.assertEqual(ui.extract_json('Sure! {"say": "ok", "ops": []} Let me know.'), {"say": "ok", "ops": []})

    def test_array_after_prose(self):
        self.assertEqual(ui.extract_json('The ops: [{"op":"remove","id":"a"},{"op":"remove","id":"b"}] done'),
                         [{"op": "remove", "id": "a"}, {"op": "remove", "id": "b"}])

    def test_brackets_in_prose_before_the_json(self):
        self.assertEqual(ui.extract_json('Change [1 of 1]: {"say":"x","ops":[{"op":"remove","id":"a"}]}'),
                         {"say": "x", "ops": [{"op": "remove", "id": "a"}]})
        self.assertEqual(ui.extract_json('Note {draft}: [{"op":"remove","id":"a"},{"op":"remove","id":"b"}]'),
                         [{"op": "remove", "id": "a"}, {"op": "remove", "id": "b"}])

    def test_strings_with_braces_and_escapes(self):
        text = 'ok {"say": "use } and { and ] \\" here", "ops": [{"op": "set", "id": "a", "props": {"text": "[x]"}}]}'
        self.assertEqual(ui.extract_json(text)["say"], 'use } and { and ] " here')
        self.assertEqual(ui.extract_json(text)["ops"][0]["props"]["text"], "[x]")

    def test_invalid(self):
        self.assertIsNone(ui.extract_json("no json here"))
        self.assertIsNone(ui.extract_json('{"unterminated": '))
        self.assertIsNone(ui.extract_json(""))

    def test_parse_ops_accepts_envelopes(self):
        self.assertEqual(ui.parse_ops('{"say":"x","ops":[{"op":"remove","id":"a"}]}'), [{"op": "remove", "id": "a"}])
        self.assertEqual(ui.parse_ops('{"op":"remove","id":"a"}'), [{"op": "remove", "id": "a"}])
        self.assertEqual(ui.parse_ops('Sure: {"op":"remove","id":"a"}'), [{"op": "remove", "id": "a"}])
        with self.assertRaises(ui.UIError):
            ui.parse_ops("nope")


# ---------------------------------------------------------------------------
# 7. ask() with a fake model
# ---------------------------------------------------------------------------


class FakeBackend(ui.Backend):
    """Scripted model: replies is a list of strings, or a function(messages) -> str."""

    name = "fake"
    model = "fake-model"

    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.systems = []
        self.lock = threading.Lock()
        self.last_usage = {"in": 1200, "out": 40, "cache_read": 1000}

    def complete(self, system, messages, max_tokens):
        with self.lock:
            self.calls.append([dict(m) for m in messages])
            self.systems.append(system)
            if callable(self.replies):
                fn = self.replies
            else:
                reply = self.replies.pop(0)
                return reply if isinstance(reply, str) else json.dumps(reply)
        return fn(messages)


class AskTest(ProjectCase):
    def ask(self, backend, request, **kw):
        with captured() as (out, err):
            res = ui.ask(self.project, backend, request, **kw)
        return res, out.getvalue()

    def timings(self):
        path = self.live.dir / "timings.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_happy_path(self):
        fake = FakeBackend([{"say": "Bigger greeting", "ops": [
            {"op": "nudge", "id": "home.hero.title", "prop": "size", "by": 1}]}])
        res, out = self.ask(fake, "make the greeting bigger")
        self.assertEqual(self.node("home.hero.title")["size"], 24)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("# Reference", fake.systems[0])
        self.assertIn("nudge", fake.systems[0])
        prompt = fake.calls[0][0]["content"]
        self.assertIn("<context>", prompt)
        self.assertIn("home.hero.title text", prompt)
        self.assertIn("Request: make the greeting bigger", prompt)
        self.assertIn("ok  Bigger greeting", out)
        self.assertEqual(res["changed"], ["home.hero.title"])
        t = [x for x in self.timings() if x["kind"] == "ask"]
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["backend"], t[0]["model"], t[0]["ops"], t[0]["tok_cache_read"]),
                         ("fake", "fake-model", 1, 1000))
        self.assertIn("model_s", t[0])
        hist = self.live.recent(1)[0]
        self.assertEqual(hist["request"], "make the greeting bigger")

    def test_one_corrective_retry(self):
        fake = FakeBackend([
            '{"say": "Blue title", "ops": [{"op": "set", "id": "home.heroo.title", "props": {"foreground": "blue"}}]}',
            '{"say": "Blue title", "ops": [{"op": "set", "id": "home.hero.title", "props": {"foreground": "blue"}}]}',
        ])
        self.ask(fake, "make the title blue")
        self.assertEqual(self.node("home.hero.title")["foreground"], "blue")
        self.assertEqual(len(fake.calls), 2)
        second = fake.calls[1]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertIn("home.heroo.title", second[1]["content"])
        self.assertIn("That failed:", second[2]["content"])
        self.assertIn("no element with id 'home.heroo.title'", second[2]["content"])
        self.assertIn("Did you mean home.hero.title", second[2]["content"])

    def test_validation_errors_also_retry(self):
        fake = FakeBackend([
            {"ops": [{"op": "set", "id": "home.hero.title", "props": {"foreground": "$brnd"}}]},
            "not json at all",
        ])
        before = self.design_bytes()
        with self.assertRaises(ui.UIError) as cm:
            self.ask(fake, "brand color title")
        self.assertIn("model ops failed: reply was not JSON", str(cm.exception))
        self.assertIn("no color token '$brnd'", fake.calls[1][2]["content"])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.design_bytes(), before)

    def test_empty_ops_escalates(self):
        fake = FakeBackend([{"say": "Maps need a Swift component", "ops": []}])
        before = self.design_bytes()
        with self.assertRaises(ui.Escalate) as cm:
            self.ask(fake, "add a map")
        self.assertIn("Maps need a Swift component", str(cm.exception))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.design_bytes(), before)

    def test_dry_run(self):
        fake = FakeBackend([{"ops": [{"op": "remove", "id": "home.section"}]}])
        before = self.design_bytes()
        res, out = self.ask(fake, "remove the header", dry=True)
        self.assertEqual(res["ops"], [{"op": "remove", "id": "home.section"}])
        self.assertIn('"remove"', out)
        self.assertEqual(self.design_bytes(), before)

    def test_parallel_variants(self):
        original = self.design_bytes()
        directions = ui.VARIANT_DIRECTIONS

        def shown():
            return (self.live.read("variants.json") or {}).get("current", -1) >= 0

        def reply(messages):
            prompt = messages[0]["content"]
            if directions[0] in prompt:  # first back: removes an element
                return json.dumps({"say": "Quiet", "ops": [{"op": "remove", "id": "home.section"}]})
            # the others only answer once the first variant is on screen, and
            # must still be judged against the design they were asked about
            self.assertTrue(wait_until(shown))
            if directions[1] in prompt:
                return json.dumps({"say": "Bold", "ops": [
                    {"op": "set", "id": "home.section", "props": {"weight": "heavy"}}]})
            return json.dumps({"say": "Airy", "ops": [{"op": "nudge", "id": "home.scroll", "prop": "spacing"}]})

        fake = FakeBackend(reply)
        res, out = self.ask(fake, "try some directions", variants=3)
        self.assertEqual(len(res["variants"]), 3, out)
        self.assertEqual(len(fake.calls), 3, "no variant should need a corrective retry")
        state = self.live.read("variants.json")
        self.assertEqual(len(state["variants"]), 3)
        self.assertEqual(state["current"], 0)
        names = [v["name"] for v in state["variants"]]
        self.assertEqual(names[0], "Quiet")
        self.assertEqual(sorted(names), ["Airy", "Bold", "Quiet"])
        self.assertNotIn("home.section", self.design().all_ids())

        def expect(name):
            ids = self.design().all_ids()
            self.assertEqual("home.section" in ids, name != "Quiet")
            if name == "Bold":
                self.assertEqual(self.node("home.section")["weight"], "heavy")
            spacing = self.node("home.scroll")["spacing"]
            self.assertEqual(spacing, "$lg" if name == "Airy" else "$md")

        with captured():
            for step in (1, 2, 0):
                self.assertEqual(ui.main(["next"]), 0)
                self.assertEqual(self.live.read("variants.json")["current"], step)
                expect(names[step])
            self.assertEqual(ui.main(["prev"]), 0)
            expect(names[2])
            self.assertEqual(ui.main(["undo"]), 0)
        self.assertEqual(self.design_bytes(), original)
        self.assertIsNone(self.live.read("variants.json"))
        self.assertIsNone(self.live.latest_snapshot())  # one undo was enough
        self.assertEqual(len(self.live.recent(10)), 1)  # cycling doesn't spam history
        t = [x for x in self.timings() if x["kind"] == "variants"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["n"], 3)

    def test_keep_variant(self):
        def reply(messages):
            return json.dumps({"ops": [{"op": "nudge", "id": "home.scroll", "prop": "spacing"}]})

        self.ask(FakeBackend(reply), "options", variants=2)
        with captured() as (out, _):
            self.assertEqual(ui.main(["keep"]), 0)
            self.assertEqual(ui.main(["keep"]), 1)
        self.assertIn("kept variant 1", out.getvalue())
        self.assertEqual(self.node("home.scroll")["spacing"], "$lg")

    def test_stats(self):
        with captured() as (out, _):
            self.assertEqual(ui.cmd_stats(self.project, argparse.Namespace()), 1)
        self.assertIn("no timings yet", out.getvalue())
        for _ in range(2):
            self.ask(FakeBackend([{"ops": [{"op": "nudge", "id": "home.section", "prop": "size"}]}]), "bigger")
        with captured() as (out, _):
            ui.cmd_apply(self.project, argparse.Namespace(ops='{"op":"nudge","id":"home.section","prop":"size"}',
                                                          note=None, no_wait=True, shot=False))
            self.assertEqual(ui.cmd_stats(self.project, argparse.Namespace()), 0)
        text = out.getvalue()
        self.assertRegex(text, r"ask\s+n=2\s+model_s p50")
        self.assertIn("cache hits 2/2", text)
        self.assertRegex(text, r"apply\s+n=1")


# ---------------------------------------------------------------------------
# 8. build_context
# ---------------------------------------------------------------------------


class ContextTest(ProjectCase):
    def select(self, nid, at=None, **extra):
        self.live.dir.mkdir(parents=True, exist_ok=True)
        payload = dict(id=nid, type="text", screen="home", at=time.time() if at is None else at, **extra)
        ui.write_atomic(self.live.dir / "selection.json", json.dumps(payload))

    def test_outline_tokens_and_other_screens(self):
        ctx = ui.build_context(self.project)
        self.assertIn('# app Demo  (tabs (app.json): 0 home "Home"→home, 1 explore "Explore"→explore, '
                      '2 profile "Profile"→profile)', ctx)
        app = json.loads((self.root / "design" / "app.json").read_text())
        app["tabs"][0]["title"] = 'Say "Hi"'
        del app["tabs"][1]["title"]
        (self.root / "design" / "app.json").write_text(json.dumps(app), encoding="utf-8")
        ctx = ui.build_context(self.project)
        self.assertIn('0 home "Say \\"Hi\\""→home, 1 explore "Explore"→explore', ctx)
        self.assertIn("# simulator: app not running", ctx)
        self.assertIn("## screen home  (design/screens/home.json)", ctx)
        self.assertIn('home.hero.title text text="Good morning" font="$title" foreground="$text"', ctx)
        self.assertIn("  home.hero vstack", ctx)
        self.assertIn("home.stories repeat items=\"data.stories\"", ctx)
        self.assertIn("(3 items)", ctx)
        self.assertIn("data.stories: 3 items, e.g.", ctx)
        self.assertIn("toolbar (home.toolbar): home.compose button", ctx)
        self.assertIn("## tokens (design/tokens.json)", ctx)
        self.assertIn("colors: brand=#5B5BD6", ctx)
        self.assertIn("spacing: xs=4 sm=8 md=16 lg=24 xl=32", ctx)
        self.assertIn("fonts: largeTitle=34/bold", ctx)
        self.assertIn("styles: card=", ctx)
        self.assertIn("state: notifications=true", ctx)
        self.assertIn("## other screens", ctx)
        for sid in ("compose", "explore", "profile", "story"):
            self.assertIn("### " + sid, ctx)
        self.assertIn("profile.signout button", ctx)

    def test_screen_argument(self):
        ctx = ui.build_context(self.project, "profile")
        self.assertIn("## screen profile", ctx)
        self.assertIn("### home", ctx)

    def test_actions_are_not_truncated(self):
        ctx = ui.build_context(self.project)
        cta = self.node("home.hero.cta")["action"]
        self.assertGreater(len(ui.compact(cta)), 70)
        self.assertIn("action=" + ui.compact(cta), ctx)
        self.assertIn("onTap=" + ui.compact(self.node("home.story")["onTap"]), ctx)

    def test_focus_from_fresh_selection(self):
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}})
        self.select("home.section", at=time.time() + 0.5)
        ctx = ui.build_context(self.project)
        self.assertIn("# picked in the simulator: home.section  inside: home.scroll", ctx)
        self.assertIn('# FOCUS ("this/it"): home.section\n', ctx)

    def test_focus_falls_back_to_last_edit(self):
        self.select("home.section", at=time.time() - 10)
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}})
        ctx = ui.build_context(self.project)
        self.assertIn("# picked in the simulator: home.section", ctx)
        self.assertIn('# FOCUS ("this/it"): home.hero.title  (last edited)', ctx)
        self.assertIn('## recent edits (newest last)\n- set home.hero.title text="Hi"', ctx)

    def test_stale_selection_is_ignored(self):
        self.select("home.section", at=time.time() - 3600)
        ctx = ui.build_context(self.project)
        self.assertNotIn("picked in the simulator", ctx)
        self.assertNotIn("FOCUS", ctx)

    def test_repeat_row_annotation(self):
        self.select("home.story.title", row=1)
        ctx = ui.build_context(self.project)
        self.assertIn("inside: home.story.text ‹ home.story ‹ home.stories ‹ home.scroll", ctx)
        self.assertIn("this is the TEMPLATE of repeat home.stories (row 2; that row's content is "
                      "data.stories.1)", ctx)

    def test_request_mentions(self):
        ctx = ui.build_context(self.project, request='make "Good morning" bolder and move the latest header')
        line = [x for x in ctx.splitlines() if x.startswith("# request mentions:")]
        self.assertEqual(len(line), 1, ctx)
        self.assertIn('"Good morning" → home.hero.title', line[0])
        self.assertIn('"Latest" → home.section', line[0])
        self.assertNotIn("request mentions", ui.build_context(self.project, request="make it pop"))

    def test_large_app_lists_other_screens_by_id(self):
        many = [{"type": "text", "text": "Row number %d with some words" % i} for i in range(400)]
        self.apply({"op": "screen", "id": "big", "spec": {"title": "Big", "body": many}})
        ctx = ui.build_context(self.project)
        self.assertIn("## other screens (ids only", ctx)
        self.assertIn("big: title=\"Big\" ids: big.row-number", ctx)

    def test_app_running(self):
        self.live.dir.mkdir(parents=True, exist_ok=True)
        ui.write_atomic(self.live.dir / "status.json", json.dumps({
            "pid": os.getpid(), "rev": 0, "screen": "story", "stack": ["home", "story"],
            "components": {"RatingStars": "stars: Int"}}))
        ctx = ui.build_context(self.project)
        self.assertIn("# simulator: showing 'story'  stack: home › story", ctx)
        self.assertIn("## screen story", ctx)
        self.assertIn("RatingStars: stars: Int", ctx)


# ---------------------------------------------------------------------------
# 9. Claude Code hook
# ---------------------------------------------------------------------------


class HookTest(ProjectCase):
    def hook(self, payload):
        stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
        with mock.patch.object(sys, "stdin", stdin), captured() as (out, err):
            code = ui.cmd_hook(self.project, argparse.Namespace())
        return code, out.getvalue(), err.getvalue()

    def pre_bash(self, command):
        code, out, _ = self.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                  "tool_input": {"command": command}})
        self.assertEqual(code, 0)
        if not out:
            return False
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        return decision["permissionDecision"] == "allow"

    def test_user_prompt_submit_adds_context(self):
        code, out, _ = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": 'make "Latest" bigger'})
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("<ui-context note="), out[:80])
        self.assertTrue(out.rstrip().endswith("</ui-context>"))
        self.assertIn("## screen home", out)
        self.assertIn('"Latest" → home.section', out)
        with mock.patch.dict(os.environ, {"UI_NO_CONTEXT_HOOK": "1"}):
            self.assertEqual(self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "x"})[1], "")
        # /ui gets the context (with mentions); other slash commands get nothing
        out = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": ' /ui make "Latest" blue'})[1]
        self.assertIn('"Latest" → home.section', out)
        self.assertEqual(self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "/help"})[1], "")
        self.assertEqual(self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "/uix"})[1], "")

    def test_pre_tool_use_allows_inert_ui_commands(self):
        allowed = [
            "./ui apply --end-turn <<'OPS'\n{\"op\":\"set\",\"id\":\"home.hero.title\",\"props\":{\"text\":\"Hi\"}}\nOPS",
            "./ui apply <<'OPS'\n[{\"op\":\"remove\",\"id\":\"x\"}]\nOPS\n",
            "./ui apply --end-turn <<'OPS'\n{\"text\": \"$(rm -rf ~) `id` it's; done\"}\nOPS",
            "./ui context",
            "./ui undo 2",
            "  ./ui status",
        ]
        for cmd in allowed:
            self.assertTrue(self.pre_bash(cmd), cmd)

    def test_pre_tool_use_does_not_allow_anything_else(self):
        denied = [
            "./ui apply <<'OPS'\n{}\nOPS\nrm -rf x",                  # command after the terminator
            "./ui apply <<'OPS'\n{}\nOPS\nrm -rf x\nOPS",             # body line equal to the delimiter
            "./ui apply; rm -rf x",
            "./ui apply && rm -rf x",
            "./ui apply | sh",
            "./ui apply `rm -rf x`",
            "./ui apply $(rm -rf x)",
            "./ui apply --note=`id` <<'OPS'\n{}\nOPS",
            "./ui apply <<OPS\n{}\nOPS",                              # unquoted heredoc expands $(...)
            "./ui apply <<\"OPS\"\n{}\nOPS",
            "./ui apply <<'OPS' && rm -rf x\n{}\nOPS",
            "./ui apply <<'OPS'\n{}",                                 # unterminated
            "./ui context\nrm -rf x",
            "./ui ask make it blue",
            "rm -rf x",
            "python3 -c 'print(1)'",
            "cd / && ./ui context",
            # bash keeps \r / \v / \f in the delimiter word, so 'OPS'\r ends at "OPS\r"
            "./ui apply <<'OPS'\r\n{}\r\nOPS\r\nrm -rf x\nOPS",
            "./ui apply <<'OPS'\x0b\n{}\nOPS\x0b\nrm -rf x\nOPS",
            "./ui apply <<'OPS'\x0c\n{}\nOPS\x0c\nrm -rf x\nOPS",
            "./ui apply <<'OPS'\u00a0\n{}\nOPS\u00a0\nrm -rf x\nOPS",    # NBSP
            "./ui apply <<'OPS'\x1f\n{}\nOPS\x1f\nrm -rf x\nOPS",
            "./ui apply <<'OPS'\u2003\n{}\nOPS\u2003\nrm -rf x\nOPS",    # em space
        ]
        for cmd in denied:
            self.assertFalse(self.pre_bash(cmd), repr(cmd))

    def test_escalation_ends_the_turn_but_other_failures_do_not(self):
        # Claude Code ignores `continue: false` from PostToolUseFailure, so with
        # --end-turn an escalation exits 0 and PostToolUse ends the turn.
        cmd = "./ui apply --end-turn <<'OPS'\n{\"say\": \"maps need Swift\", \"ops\": []}\nOPS"
        before = self.design_bytes()
        args = argparse.Namespace(ops='{"say": "maps need Swift", "ops": []}', note=None, no_wait=False,
                                  shot=False, end_turn=True)
        with captured() as (out, _):
            self.assertEqual(ui.cmd_apply(self.project, args), 0)
        self.assertIn("ESCALATE: maps need Swift", out.getvalue())
        self.assertEqual(self.design_bytes(), before)
        code, out, _ = self.post_bash(cmd)
        self.assertEqual(code, 0)
        stop = json.loads(out)
        self.assertIs(stop["continue"], False)
        self.assertIn("ESCALATE: maps need Swift", stop["stopReason"])
        # a later successful apply is not mistaken for an escalation
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}}, note="Friendlier")
        later = "./ui apply --end-turn <<'OPS'\n{\"say\": \"Friendlier\", \"ops\": []}\nOPS"
        self.assertNotIn("ESCALATE", json.loads(self.post_bash(later)[1])["stopReason"])
        # failures (rejected ops) never end the turn, so the model can retry
        self.assertEqual(self.hook({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                                    "tool_input": {"command": cmd},
                                    "error": "Exit code 1\nerror rejected, nothing was written"})[1], "")
        # without --end-turn an escalation is still exit 3
        args.end_turn = False
        with captured(), self.assertRaises(ui.Escalate):
            ui.cmd_apply(self.project, args)

    def test_other_tools_and_bad_payloads_are_ignored(self):
        self.assertEqual(self.hook({"hook_event_name": "PreToolUse", "tool_name": "Read",
                                    "tool_input": {"file_path": "x"}}), (0, "", ""))
        self.assertEqual(self.hook("not json"), (0, "", ""))
        self.assertEqual(self.hook(""), (0, "", ""))

    def post_edit(self, path, tool="Edit"):
        return self.hook({"hook_event_name": "PostToolUse", "tool_name": tool,
                          "tool_input": {"file_path": str(path)}})

    def test_post_edit_invalid_design_json(self):
        path = self.root / "design" / "screens" / "home.json"
        path.write_text('{"id": "home", "body": ', encoding="utf-8")
        code, out, err = self.post_edit(path)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("design validation failed", err)
        self.assertIn("screens/home.json: invalid JSON", err)

    def test_post_write_invalid_design(self):
        path = self.root / "design" / "screens" / "home.json"
        data = json.loads(path.read_text())
        data["body"]["children"][1]["type"] = "txt"
        path.write_text(json.dumps(data), encoding="utf-8")
        code, _, err = self.post_edit(path, tool="Write")
        self.assertEqual(code, 2)
        self.assertIn('unknown type "txt"', err)

    def test_post_edit_valid_or_unrelated(self):
        self.assertEqual(self.post_edit(self.root / "design" / "screens" / "home.json"), (0, "", ""))
        other = self.root / "notes.json"
        other.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.post_edit(other), (0, "", ""))
        (self.root / "design" / "screens" / "home.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.post_edit(self.root / "design" / "README.md")[0], 0)

    def post_bash(self, command):
        return self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "tool_input": {"command": command}})

    def test_post_bash_end_turn(self):
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}}, note="Friendlier greeting")
        cmd = "./ui apply --end-turn <<'OPS'\n{\"say\": \"Friendlier greeting\", \"ops\": []}\nOPS"
        code, out, _ = self.post_bash(cmd)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIs(data["continue"], False)
        self.assertIn("Friendlier greeting", data["stopReason"])
        self.assertIn("app not running", data["stopReason"])
        self.assertEqual(self.post_bash(cmd.replace(" --end-turn", "")), (0, "", ""))
        self.assertEqual(self.post_bash("./ui context --end-turn")[1], "")
        # a record written by another call (say text not in this command) is ignored
        self.assertEqual(self.post_bash("./ui apply --end-turn <<'OPS'\n{\"say\": \"Other\"}\nOPS"), (0, "", ""))

    def test_rejected_end_turn_apply_clears_the_old_record(self):
        good = argparse.Namespace(ops='{"say": "maps need Swift", "ops": []}', note=None, no_wait=False,
                                  shot=False, end_turn=True)
        with captured():
            ui.cmd_apply(self.project, good)
        self.assertTrue((self.live.dir / "last-apply.json").exists())
        bad = argparse.Namespace(ops='{"say": "maps need Swift", "ops": [{"op": "set", "id": "nope"}]}',
                                 note=None, no_wait=False, shot=False, end_turn=True)
        with captured(), self.assertRaises(ui.UIError):
            ui.cmd_apply(self.project, bad)
        self.assertFalse((self.live.dir / "last-apply.json").exists())
        # e.g. `./ui apply --end-turn <<'OPS' ... | tail -5` exits 0 even though apply failed
        cmd = "./ui apply --end-turn <<'OPS'\n{\"say\": \"maps need Swift\", \"ops\": []}\nOPS"
        self.assertEqual(self.post_bash(cmd), (0, "", ""))

    def test_post_bash_end_turn_needs_a_recent_apply(self):
        self.assertEqual(self.post_bash("./ui apply --end-turn <<'OPS'\n{}\nOPS"), (0, "", ""))
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Hi"}})
        info = json.loads((self.live.dir / "last-apply.json").read_text())
        info["at"] -= 120
        (self.live.dir / "last-apply.json").write_text(json.dumps(info))
        self.assertEqual(self.post_bash("./ui apply --end-turn <<'OPS'\n{}\nOPS"), (0, "", ""))


class DesignerTest(ProjectCase):
    """The designer loop: instant words (no model), redo, options, the brief, round stats."""

    def hook(self, payload):
        stdin = io.StringIO(json.dumps(payload))
        with mock.patch.object(sys, "stdin", stdin), captured() as (out, _):
            code = ui.cmd_hook(self.project, argparse.Namespace())
        self.assertEqual(code, 0)
        return out.getvalue()

    def say(self, prompt):
        """A designer prompt; returns the instant reply, or None if it would reach Claude."""
        out = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        if out.startswith("{"):
            reply = json.loads(out)
            self.assertIs(reply["continue"], False)
            return reply["stopReason"]
        self.assertIn("<ui-context", out)
        return None

    def title(self):
        return self.node("home.hero.title").get("text")

    def options_envelope(self):
        return {"say": "Make the greeting calmer", "options": [
            {"say": "Soft", "ops": [{"op": "set", "id": "home.hero.title", "props": {"text": "Soft"}}]},
            {"say": "Airy", "ops": [{"op": "set", "id": "home.hero.title", "props": {"text": "Airy"}}]},
            {"say": "Broken", "ops": [{"op": "set", "id": "no.such.id", "props": {"text": "x"}}]},
        ]}

    def apply_cli(self, envelope, end_turn=True):
        args = argparse.Namespace(ops=json.dumps(envelope), note=None, end_turn=end_turn, no_wait=True, shot=False)
        with captured() as (out, _):
            code = ui.cmd_apply(self.project, args)
        return code, out.getvalue()

    # instant words ---------------------------------------------------------

    def test_instant_word_mapping(self):
        word = lambda p: ui.instant_word(self.project, p)
        for prompt in ("undo", "Undo.", "please undo", "ok, undo that!", "/ui undo", "go back", "Revert it"):
            self.assertEqual(word(prompt), "undo", prompt)
        for prompt in ("redo", "Put it back", "bring it back"):
            self.assertEqual(word(prompt), "redo", prompt)
        self.assertEqual(word("select"), "select")
        self.assertEqual(word("let me pick"), "select")
        # option words without options on screen get a short explanation, not a model turn
        expected = {"next": "nooptions:next", "previous": "nooptions:prev", "keep": "nooptions:keep",
                    "this one": "nooptions:keep", "option 2": "nooptions:option", "2": None}
        for prompt, want in expected.items():
            self.assertEqual(word(prompt), want, prompt)
        for prompt in ("make the title bigger", "undo the color but keep the size", "", "   ", "x" * 60):
            self.assertIsNone(word(prompt), prompt)
        self.live.dir.mkdir(parents=True, exist_ok=True)
        (self.live.dir / "variants.json").write_text("{}", encoding="utf-8")
        expected = {"next": "next", "Another one": "next", "previous": "prev", "go back": "prev",
                    "back": "prev", "undo": "undo", "option 2": "option:2", "#3": "option:3", "2": "option:2",
                    "show me 1": "option:1", "keep this one": "keep", "Let’s go with this.": "keep",
                    "I like that one": "keep", "option 7": None}
        for prompt, want in expected.items():
            self.assertEqual(word(prompt), want, prompt)

    def test_undo_and_redo_are_instant_and_logged(self):
        before = self.design_bytes()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Changed"}}, note="new title")
        after = self.design_bytes()
        t0 = time.perf_counter()
        reply = self.say("undo")
        self.assertLess(time.perf_counter() - t0, 1.0, "no app running, so nothing to wait for")
        self.assertEqual(reply, "↩ Undid: new title")
        self.assertEqual(self.design_bytes(), before)
        self.assertEqual(self.say("Redo that."), "↪ Redid: new title")
        self.assertEqual(self.design_bytes(), after)
        self.assertEqual(self.say("redo"), "Nothing to redo.")
        self.assertEqual(self.say("undo"), "↩ Undid: new title")
        self.assertEqual(self.say("undo"), "Nothing to undo.")
        self.assertIsNone(self.say("make the title bigger"))
        lines = [json.loads(l) for l in (self.live.dir / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([l["instant"] for l in lines], ["undo", "redo", "redo", "undo", "undo", None])
        # the escape hatch sends every word to Claude
        with mock.patch.dict(os.environ, {"UI_NO_INSTANT": "1"}):
            self.assertIsNone(self.say("redo"))
        self.assertEqual(self.design_bytes(), before)

    def test_option_words_without_options(self):
        before = self.design_bytes()
        self.assertIn("no options on screen", self.say("next"))
        self.assertIn("no options on screen", self.say("option 2"))
        self.assertEqual(self.say("keep that one"), "Saved. Every change stays until you say “undo”.")
        self.assertEqual(self.design_bytes(), before)

    def test_ask_hint(self):
        feeling = ("It feels cold and clinical", "make it more premium", "I'm not sure about the button, show me ideas",
                   "The intro sounds like a bank", "make the total feel like an achievement", "calmer please")
        for prompt in feeling:
            self.assertIn('send 3 "options" that look different at a glance', ui.ask_hint(prompt), prompt)
        for prompt in ("From now on, never use black text", "I prefer serif type", "always keep cards dark"):
            self.assertIn("lasting preference", ui.ask_hint(prompt), prompt)
        for prompt in ("make the title bigger", "rename the button to Start", "put the icon next to the name",
                       "make the score label bold"):
            self.assertEqual(ui.ask_hint(prompt), "", prompt)
        out = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "make it feel calmer"})
        self.assertTrue(out.rstrip().endswith('unless it names one exact change\n</ui-context>'), out[-200:])
        out = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": "make the title bigger"})
        self.assertNotIn("options", out.split("## screen")[0])

    def test_select_needs_the_app(self):
        self.assertIsNone(self.say("select"))  # app not running: Claude explains
        self.assertFalse((self.live.dir / "pick").exists())
        self.live.dir.mkdir(parents=True, exist_ok=True)
        ui.write_atomic(self.live.dir / "status.json", json.dumps({"pid": os.getpid(), "rev": 0}))
        self.assertIn("Tap the element", self.say("select"))
        self.assertTrue((self.live.dir / "pick").exists())

    # redo ------------------------------------------------------------------

    def redo(self, steps=1):
        with captured() as (out, _):
            code = ui.cmd_redo(self.project, argparse.Namespace(steps=steps))
        return code, out.getvalue()

    def undo(self, steps=1):
        with captured():
            return ui.cmd_undo(self.project, argparse.Namespace(steps=steps))

    def test_redo_steps_and_undo_after_redo(self):
        base = self.design_bytes()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "One"}}, note="one")
        one = self.design_bytes()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Two"}}, note="two")
        two = self.design_bytes()
        self.undo(2)
        self.assertEqual(self.design_bytes(), base)
        code, out = self.redo()
        self.assertEqual((code, out.strip()), (0, "ok  redid: one"))
        self.assertEqual(self.design_bytes(), one)
        self.redo()
        self.assertEqual(self.design_bytes(), two)
        self.assertEqual(self.redo()[0], 1)
        self.undo()  # redo re-took a snapshot, so undo still works
        self.assertEqual(self.design_bytes(), one)

    def test_new_change_clears_redo(self):
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "One"}})
        self.undo()
        self.apply({"op": "set", "id": "home.hero.title", "props": {"text": "Other"}})
        code, out = self.redo()
        self.assertEqual((code, out.strip()), (1, "nothing to redo"))
        self.assertEqual(self.title(), "Other")

    # options ---------------------------------------------------------------

    def test_options_flip_keep_and_undo(self):
        base_title = self.title()
        before = self.design_bytes()
        command = "./ui apply --end-turn <<'OPS'\n" + json.dumps(self.options_envelope()) + "\nOPS"
        code, out = self.apply_cli(self.options_envelope())
        self.assertEqual(code, 0)
        self.assertIn("skipped option 3 (Broken)", out)
        self.assertEqual(self.title(), "Soft")
        state = json.loads((self.live.dir / "variants.json").read_text(encoding="utf-8"))
        self.assertEqual([v["name"] for v in state["variants"]], ["Soft", "Airy"])
        # the end-turn hook replies with the options, not a summary
        end = json.loads(self.hook({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                    "tool_input": {"command": command}}))
        self.assertIs(end["continue"], False)
        self.assertIn("2 options (1 Soft · 2 Airy). Showing 1.", end["stopReason"])
        self.assertIn("Option 2 of 2: Airy", self.say("next"))
        self.assertEqual(self.title(), "Airy")
        self.assertIn("Option 1 of 2: Soft", self.say("next"))  # wraps around
        self.assertIn("Option 2 of 2", self.say("previous"))
        self.assertIn("Option 1 of 2", self.say("option 1"))
        self.assertEqual(self.say("option 4"), "There are only 2 options.")
        self.assertEqual(self.title(), "Soft")
        self.assertEqual(self.say("keep this one"), "✓ Kept option 1: Soft")
        self.assertFalse((self.live.dir / "variants.json").exists())
        self.assertIn("no options on screen", self.say("next"))  # options are done
        self.assertEqual(self.title(), "Soft")
        self.assertEqual(self.say("undo"), "↩ Undid: Make the greeting calmer [variant 1: Soft]")
        self.assertEqual(self.title(), base_title)
        self.assertEqual(self.design_bytes(), before, "one undo returns to the design before the options")

    def test_flipping_options_takes_one_undo(self):
        before = self.design_bytes()
        self.apply_cli(self.options_envelope())
        for _ in range(3):
            self.say("next")
        self.say("undo")  # while options are on screen, undo drops them all
        self.assertEqual(self.design_bytes(), before)
        self.assertFalse((self.live.dir / "variants.json").exists())

    def test_options_all_invalid_writes_nothing(self):
        before = self.design_bytes()
        env = {"say": "Try", "options": [{"say": "Bad", "ops": [{"op": "remove", "id": "nope"}]}, "junk"]}
        with self.assertRaises(ui.UIError) as cm:
            self.apply_cli(env)
        self.assertIn("no option is valid", str(cm.exception))
        self.assertIn("option 2: must be", str(cm.exception))
        self.assertEqual(self.design_bytes(), before)
        self.assertFalse((self.live.dir / "variants.json").exists())

    def test_options_are_each_applied_to_the_base(self):
        env = {"say": "Two looks", "options": [
            {"say": "Dark", "ops": [{"op": "path", "file": "app", "path": "colorScheme", "value": "dark"}]},
            {"say": "Renamed", "ops": [{"op": "set", "id": "home.hero.title", "props": {"text": "Hey"}}]}]}
        scheme = self.design().app.get("colorScheme")
        self.apply_cli(env)
        self.assertEqual(self.design().app.get("colorScheme"), "dark")
        self.say("next")
        self.assertEqual(self.design().app.get("colorScheme"), scheme, "option 2 must not carry option 1")
        self.assertEqual(self.title(), "Hey")

    # brief -----------------------------------------------------------------

    def test_brief_in_context_and_appendable(self):
        self.assertNotIn("designer's brief", ui.build_context(self.project))
        self.apply({"op": "path", "file": "app", "path": "brief.-", "value": "Serif type everywhere"})
        self.apply({"op": "path", "file": "app", "path": "brief.-", "value": "Calm, never alarming"})
        self.assertEqual(self.design().app["brief"], ["Serif type everywhere", "Calm, never alarming"])
        ctx = ui.build_context(self.project)
        self.assertIn("# designer's brief", ctx)
        self.assertIn("#   - Serif type everywhere\n#   - Calm, never alarming", ctx)
        self.assertLess(ctx.index("designer's brief"), ctx.index("## screen"), "the brief heads the context")

    def test_brief_validation(self):
        for bad in ("Serif", ["ok", ""], ["x" * 301], [1]):
            self.assertRejected({"op": "path", "file": "app", "path": "brief", "value": bad}, "brief")

    # stats -----------------------------------------------------------------

    def test_stats_counts_rounds(self):
        for prompt, word in (("make it calmer", None), ("next", "next"), ("keep", "keep"),
                             ("bigger title", None), ("undo", "undo")):
            ui.log_prompt(self.project, prompt, word)
        with captured() as (out, _):
            self.assertEqual(ui.cmd_stats(self.project, argparse.Namespace()), 0)
        line = out.getvalue()
        self.assertIn("prompts 5 (to Claude 2, instant 3)", line)
        self.assertIn("undo 1 (50% of changes)", line)
        self.assertIn("options kept 1", line)


class ClaudeCLIBackendTest(ProjectCase):
    def run_with(self, *results):
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw))
            code, out, err = results[min(len(calls), len(results)) - 1]
            return subprocess.CompletedProcess(cmd, code, out, err)

        backend = ui.ClaudeCLIBackend(self.project, ui.DEFAULT_MODEL)
        with mock.patch.object(ui.subprocess, "run", side_effect=fake_run):
            try:
                return backend.complete("sys", [{"role": "user", "content": "hi"}], 100), calls
            except ui.UIError as e:
                return e, calls

    def test_success_runs_lean_outside_the_project(self):
        out, calls = self.run_with((0, json.dumps({"result": '{"ops": []}', "is_error": False}), ""))
        self.assertEqual(out, '{"ops": []}')
        cmd, kw = calls[0]
        self.assertIn("--setting-sources", cmd)
        self.assertNotIn("--settings", cmd)  # nothing to forward in a fresh scaffold
        self.assertNotEqual(kw["cwd"], str(self.root))
        self.assertEqual(kw["env"]["UI_NO_CONTEXT_HOOK"], "1")

    def test_login_error_is_shown_not_the_json_prefix(self):
        blob = json.dumps({"duration_api_ms": 0, "usage": {"x": "y" * 900}, "is_error": True,
                           "api_error_status": 401, "result": "Not logged in · Please run /login"})
        err, _ = self.run_with((1, blob, ""))
        self.assertIsInstance(err, ui.UIError)
        self.assertIn("Not logged in", str(err))
        self.assertIn("HTTP 401", str(err))

    def test_older_cli_retries_with_plain_flags_from_the_project(self):
        out, calls = self.run_with((1, "", "error: unknown option '--strict-mcp-config'"),
                                   (0, json.dumps({"result": "ok"}), ""))
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--setting-sources", calls[1][0])
        self.assertEqual(calls[1][1]["cwd"], str(self.root))

    def test_project_provider_settings_are_forwarded(self):
        (self.root / ".claude").mkdir(exist_ok=True)
        (self.root / ".claude" / "settings.local.json").write_text(
            json.dumps({"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}, "apiKeyHelper": "~/bin/key"}), encoding="utf-8")
        _, calls = self.run_with((0, json.dumps({"result": "ok"}), ""))
        cmd = calls[0][0]
        forwarded = json.loads(cmd[cmd.index("--settings") + 1])
        self.assertEqual(forwarded, {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}, "apiKeyHelper": "~/bin/key"})


class EvalCheckTest(ProjectCase):
    def check(self, exp, ops=(), escalated=False):
        base = ui.Design(self.project)
        for op in ops:
            self.apply(op)
        return ui._check(ui.Design(self.project), base, exp, escalated)

    def test_hidden_accepts_removal_or_visible_if(self):
        exp = {"id": "profile.email", "hidden": True}
        self.assertEqual(self.check(exp), "profile.email still visible")
        self.assertEqual(self.check(exp, [{"op": "remove", "id": "profile.email"}]), "")

    def test_hidden_by_visible_if(self):
        exp = {"id": "profile.email", "hidden": True}
        self.assertEqual(self.check(exp, [{"op": "set", "id": "profile.email", "props": {"visibleIf": "showEmail"}}]), "")

    def test_missing_without_hidden_still_fails(self):
        self.assertEqual(self.check({"id": "home.nope", "prop": "text", "eq": "x"}), "home.nope missing")

    def test_size_gt_uses_the_rendered_size(self):
        exp = {"id": "home.hero.title", "size_gt": 22}  # font $title = 22
        self.assertEqual(self.check(exp), "home.hero.title text size 22")
        self.assertEqual(self.check(exp, [{"op": "set", "id": "home.hero.title", "props": {"font": "$largeTitle"}}]), "")

    def test_size_gt_explicit_size_wins_over_font(self):
        exp = {"id": "home.hero.title", "size_gt": 24}
        ops = [{"op": "set", "id": "home.hero.title", "props": {"size": 24}},
               {"op": "set", "id": "home.hero.title", "props": {"font": "$largeTitle"}}]
        self.assertEqual(self.check(exp, ops), "home.hero.title text size 24")

    def test_app_path_and_escalation(self):
        exp = {"app": "tabs.1.title", "eq": "Discover"}
        self.assertEqual(self.check(exp, [{"op": "path", "file": "app", "path": "tabs.1.title",
                                           "value": "Discover"}]), "")
        self.assertEqual(self.check(exp, escalated=True), "escalated")


# ---------------------------------------------------------------------------
# 10. Drift: the CLI's vocabulary must match what the Swift runtime reads
# ---------------------------------------------------------------------------


def swift_source(name):
    """Swift source with // comments removed (not inside "https://..." strings)."""
    text = (RUNTIME / name).read_text(encoding="utf-8")
    return re.sub(r"(^|\s)//.*$", r"\1", text, flags=re.M)


def swift_block(src, marker):
    """Text inside the {...} block that follows `marker` (string literals skipped)."""
    at = src.find(marker)
    if at < 0:
        raise AssertionError(f"{marker!r} not found in Swift source")
    start = src.index("{", at + len(marker) - 1 if marker.endswith("{") else at + len(marker))
    depth, i, in_str = 0, start, False
    while i < len(src):
        ch = src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
        i += 1
    raise AssertionError(f"unbalanced block after {marker!r}")


def swift_cases(block):
    """String literals in the `case ...:` labels of a switch body."""
    out = set()
    for label in re.findall(r"^\s*case (.+?):", block, flags=re.M):
        out.update(re.findall(r'"(\w+)"', label))
    return out


class DriftTest(unittest.TestCase):
    # Keys Swift reads with p[...] that are not node props in the CLI vocabulary.
    # (Empty today: structural keys — type/id/children/template — are stripped by
    # LiveNode.parse, so Swift never reads them through p[...]. Add a key here only
    # with a comment saying why it is not a documented prop.)
    SWIFT_ONLY_PROPS = set()

    @classmethod
    def setUpClass(cls):
        cls.renderer = swift_source("LiveRenderer.swift")
        cls.theme = swift_source("LiveTheme.swift")
        cls.root = swift_source("LiveRoot.swift")
        cls.store = swift_source("LiveStore.swift")
        cls.cli_props = set(ui.COMMON_PROPS).union(*ui.TYPE_PROPS.values())

    def swift_props(self):
        props = set()
        for src in (self.renderer, self.theme, self.root):
            props.update(re.findall(r'\b(?:p|props|labelProps)\["(\w+)"\]', src))
        return props

    def test_swift_props_are_known_to_the_cli(self):
        swift = self.swift_props()
        self.assertGreater(len(swift), 40)  # the regex really found the reads
        self.assertEqual(swift - self.cli_props - self.SWIFT_ONLY_PROPS, set(),
                         "Swift reads props the CLI would reject as unknown")

    def test_every_cli_prop_is_rendered(self):
        self.assertEqual(self.cli_props - self.swift_props(), set(),
                         "CLI accepts props that the Swift renderer ignores")

    def test_node_types(self):
        cases = swift_cases(swift_block(self.renderer, "func content("))
        render = swift_block(self.renderer, "func render()")
        special = set(re.findall(r'node\.type == "(\w+)"', render))
        self.assertEqual(special, {"button"})
        self.assertIn("repeat", cases)
        self.assertIn("custom", cases)
        self.assertEqual(set(ui.NODE_TYPES), cases | special)

    def test_container_types(self):
        literal = re.search(r"static let containerTypes: Set<String> = \[(.*?)\]", self.renderer).group(1)
        swift = set(re.findall(r'"(\w+)"', literal))
        self.assertEqual(swift, set(ui.CONTAINERS) | {"repeat"})

    def test_action_verbs(self):
        verbs = set(re.findall(r'\ba\["(\w+)"\]', swift_block(self.store, "func perform(")))
        self.assertEqual(set(ui.ACTION_VERBS), verbs - {"params"})  # params modifies navigate/sheet

    def test_every_node_type_is_in_the_reference(self):
        ref = REFERENCE.read_text(encoding="utf-8")
        missing = [t for t in sorted(ui.NODE_TYPES) if "`%s`" % t not in ref]
        self.assertEqual(missing, [])

    def test_enum_values_match_swift(self):
        r, t = self.renderer, self.theme

        def switch_on(src, prop):
            return swift_cases(swift_block(src, 'switch theme.text(p["%s"])' % prop))

        variants = set(re.findall(r'"(\w+)"', re.search(
            r"static let buttonVariants: Set<String> = \[(.*?)\]", r).group(1)))
        # (cli values, swift cases, values handled by the Swift `default:` branch)
        table = {
            "controlSize": (ui.ENUMS["controlSize"], switch_on(r, "controlSize"), set()),
            "keyboard": (ui.ENUMS["keyboard"], switch_on(r, "keyboard"), {"default"}),
            "pickerStyle": (ui.ENUMS["pickerStyle"], switch_on(r, "pickerStyle"), {"segmented"}),
            "listStyle": (ui.ENUMS["listStyle"], switch_on(r, "listStyle"), {"insetGrouped"}),
            "symbolMode": (ui.ENUMS["symbolMode"], switch_on(r, "symbolMode"), set()),
            "shape": (ui.ENUMS["shape"], switch_on(r, "shape"), {"rect"}),
            "textAlign": (ui.ENUMS["textAlign"], switch_on(r, "textAlign"), set()),
            "variant": (ui.ENUMS["variant"], variants, set()),
            "weight": (ui.ENUMS["weight"], swift_cases(swift_block(t, "static func weight(")), set()),
            "design": (ui.ENUMS["design"], swift_cases(swift_block(t, "static func design(")), set()),
            "text styles": (ui.TEXT_STYLES, swift_cases(swift_block(t, "static func textStyle(")), set()),
            "named colors": (ui.NAMED_COLORS, swift_cases(swift_block(t, "static func named(")), set()),
            "frameAlign": (ui.ENUMS["frameAlign"], swift_cases(swift_block(r, "static func alignment(")), set()),
            "vstack align": (ui.ALIGN_BY_TYPE["vstack"], swift_cases(swift_block(r, "static func hAlign(")),
                             {"center"}),
            "hstack align": (ui.ALIGN_BY_TYPE["hstack"], swift_cases(swift_block(r, "static func vAlign(")),
                             {"center"}),
        }
        for name, (cli, swift, defaults) in table.items():
            with self.subTest(name):
                self.assertTrue(swift, "regex found no Swift cases")
                self.assertEqual(set(swift) - set(cli), set(), "Swift handles values the CLI rejects")
                self.assertEqual(set(cli) - set(swift), set(defaults), "CLI accepts values Swift ignores")


# ---------------------------------------------------------------------------
# 11. CLI smoke (subprocess)
# ---------------------------------------------------------------------------


class ShotPathTest(ProjectCase):
    def test_screenshot_path_must_stay_inside_the_project(self):
        outside = argparse.Namespace(path="/tmp/elsewhere.png", small=False)
        with self.assertRaises(ui.UIError) as cm:
            ui.cmd_shot(self.project, outside)
        self.assertIn("inside the project", str(cm.exception))
        inside = argparse.Namespace(path=str(self.root / ".live" / "x.png"), small=False)
        with mock.patch.object(ui, "take_screenshot", return_value="ok") as shot, captured():
            self.assertEqual(ui.cmd_shot(self.project, inside), 0)
        shot.assert_called_once()


class CliSmokeTest(ProjectCase):
    def run_ui(self, *args, stdin=None):
        env = dict(os.environ, UI_PROJECT_DIR=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(self.root / "ui")] + list(args), input=stdin, cwd=str(self.root),
                              env=env, capture_output=True, text=True, timeout=60)

    def test_validate(self):
        p = self.run_ui("validate")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"^ok  5 screens, \d+ elements valid")
        (self.root / "design" / "tokens.json").write_text("[]", encoding="utf-8")
        p = self.run_ui("validate")
        self.assertEqual(p.returncode, 1)
        self.assertIn("tokens.json: top level must be an object", p.stderr)

    def test_context(self):
        p = self.run_ui("context", "--screen", "explore", "--request", "the 'Search topics' field")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("## screen explore", p.stdout)
        self.assertIn('"Search topics" → explore.search', p.stdout)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_apply_heredoc_with_apostrophe(self):
        script = ("./ui apply --end-turn <<'OPS'\n"
                  '{"say": "Friendlier", "ops": [{"op": "set", "id": "home.hero.title", '
                  '"props": {"text": "It\'s a $good `day` $(x)"}}]}\n'
                  "OPS\n")
        env = dict(os.environ, UI_PROJECT_DIR=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run(["bash", "-c", script], cwd=str(self.root), env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('ok  set home.hero.title text="It\'s a $good `day` $(x)"', p.stdout)
        self.assertEqual(self.node("home.hero.title")["text"], "It's a $good `day` $(x)")
        self.assertEqual(self.live.recent(1)[0]["request"], "Friendlier")

    def test_apply_from_stdin_and_exit_codes(self):
        p = self.run_ui("apply", "--no-wait", stdin='[{"op":"nudge","id":"home.section","prop":"size"}]')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.node("home.section")["size"], 20)
        before = self.design_bytes()
        p = self.run_ui("apply", '{"op":"set","id":"home.nope","props":{"text":"x"}}')
        self.assertEqual(p.returncode, 1)
        self.assertIn("error op #1 (set): no element with id 'home.nope'", p.stderr)
        p = self.run_ui("apply", '{"op":"set","id":"home.section","props":{"foreground":"$nope"}}')
        self.assertEqual(p.returncode, 1)
        self.assertIn("rejected, nothing was written", p.stderr)
        p = self.run_ui("apply", "not json")
        self.assertEqual(p.returncode, 1)
        p = self.run_ui("apply", '{"say":"Charts need a Swift component","ops":[]}')
        self.assertEqual(p.returncode, 3)
        self.assertIn("ESCALATE: Charts need a Swift component", p.stdout)
        self.assertEqual(self.design_bytes(), before)
        self.assertEqual(self.run_ui("undo").returncode, 0)
        self.assertEqual(self.run_ui("undo").returncode, 1)

    def test_reference_and_help(self):
        p = self.run_ui("reference")
        self.assertEqual(p.returncode, 0)
        self.assertIn("# Live UI reference", p.stdout)
        p = self.run_ui()
        self.assertEqual(p.returncode, 0)
        self.assertIn("./ui apply", p.stdout)


if __name__ == "__main__":
    unittest.main()
