#!/usr/bin/env python3
"""bench_build.py — times the Swift build paths of a scaffolded app on this Mac.

Compares what ./run.sh does (xcodebuild, one arch, no index store) with xtool
(https://github.com/xtool-org/xtool, a SwiftPM-based xcodebuild replacement):
clean build, no-op rebuild, one-file edit, then install + launch on a booted
Simulator. For scale it also times one hot-reload `./ui apply` (no build).

Usage:  python3 scripts/bench_build.py <app dir> [--runs 3] [--udid <booted UDID>]
Needs Xcode 26+, xcodegen, and `brew install xtool-org/tap/xtool` for the xtool rows.
The xtool variant is built from a copy (<app>-xtool/) with a generated Package.swift.
"""
import argparse
import json
import os
import plistlib
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

EDIT_FILE = Path("Sources/Components/Components.swift")
EDIT_A, EDIT_B = "HStack(spacing: 2)", "HStack(spacing: 3)"


LAST_OUTPUT = {"text": ""}


def run(cmd, cwd, env=None, timeout=1800):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    secs = time.perf_counter() - t0
    LAST_OUTPUT["text"] = p.stdout + p.stderr
    if p.returncode != 0:
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-40:])
        print(f"!! failed ({secs:.1f}s): {' '.join(cmd)}\n{tail}", flush=True)
        return None
    return secs


def toggle_edit(root: Path) -> None:
    path = root / EDIT_FILE
    text = path.read_text()
    text = text.replace(EDIT_A, EDIT_B) if EDIT_A in text else text.replace(EDIT_B, EDIT_A)
    path.write_text(text)


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(x):
    return "failed" if x is None else f"{x:.2f}s"


class XcodeBuild:
    """xcodebuild as run.sh calls it (generic Simulator destination), or with `-sdk`
    instead of `-destination` (skips destination lookup)."""

    def __init__(self, root: Path, scheme: str, sdk_only: bool = False):
        self.root, self.scheme, self.sdk_only = root, scheme, sdk_only
        self.name = "xcodebuild -sdk" if sdk_only else "xcodebuild (run.sh)"
        self.derived = "build-sdk" if sdk_only else "build"
        self.app = root / self.derived / "Build/Products/Debug-iphonesimulator" / f"{scheme}.app"

    def clean(self):
        shutil.rmtree(self.root / self.derived, ignore_errors=True)

    def build(self, timing: bool = False):
        where = ["-sdk", "iphonesimulator"] if self.sdk_only else ["-destination", "generic/platform=iOS Simulator"]
        extra = ["-showBuildTimingSummary"] if timing else []
        return run(["xcodebuild", "-project", f"{self.scheme}.xcodeproj", "-scheme", self.scheme,
                    "-configuration", "Debug"] + where + ["-derivedDataPath", self.derived] + extra +
                   ["ARCHS=arm64", "ONLY_ACTIVE_ARCH=YES", "COMPILER_INDEX_STORE_ENABLE=NO", "build"], self.root)


class XTool:
    name = "xtool (SwiftPM)"

    def __init__(self, root: Path, scheme: str):
        self.root, self.scheme = root, scheme
        self.app = root / "xtool" / f"{scheme}.app"

    def clean(self):
        shutil.rmtree(self.root / ".build", ignore_errors=True)
        shutil.rmtree(self.root / "xtool", ignore_errors=True)

    def build(self, timing: bool = False):
        return run(["xtool", "dev", "build", "--triple", "arm64-apple-ios-simulator"], self.root)


def make_xtool_copy(app_dir: Path, scheme: str, bundle_id: str) -> Path:
    dest = app_dir.parent / f"{app_dir.name}-xtool"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir()
    for name in ("Sources", "design", "tools", "ui"):
        src = app_dir / name
        if src.is_dir():
            shutil.copytree(src, dest / name)
        elif src.exists():
            shutil.copy2(src, dest / name)
    (dest / "Package.swift").write_text(f"""// swift-tools-version: 6.0
import PackageDescription

// xtool builds the package's single library product into an iOS app.
let package = Package(
    name: "{scheme}",
    platforms: [.iOS("26.0"), .macOS(.v14)],
    products: [.library(name: "{scheme}", targets: ["{scheme}"])],
    targets: [
        .target(name: "{scheme}", path: "Sources", swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
""")
    (dest / "xtool.yml").write_text(f"version: 1\n\nbundleID: {bundle_id}.xtool\n")
    return dest


def bundle_id_of(app: Path):
    try:
        with (app / "Info.plist").open("rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return None


def install_and_launch(app: Path, root: Path, udid: str):
    """(install s, launch-until-live s): live = the app wrote .live/status.json."""
    bid = bundle_id_of(app)
    if not bid:
        print(f"!! no Info.plist bundle id in {app}")
        return None, None
    t_install = run(["xcrun", "simctl", "install", udid, str(app)], root)
    status = root / ".live" / "status.json"
    try:
        status.unlink()
    except FileNotFoundError:
        pass
    env = dict(os.environ, SIMCTL_CHILD_UI_PROJECT_DIR=str(root))
    t0 = time.perf_counter()
    if run(["xcrun", "simctl", "launch", "--terminate-running-process", udid, bid, "-UIProjectDir", str(root)],
           root, env=env) is None:
        return t_install, None
    while time.perf_counter() - t0 < 30:
        if status.exists():
            return t_install, time.perf_counter() - t0
        time.sleep(0.05)
    print(f"!! {bid} launched but never wrote {status} (not live)")
    return t_install, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--udid", default="booted")
    args = ap.parse_args()
    app_dir = Path(args.app_dir).resolve()
    yml = (app_dir / "project.yml").read_text()
    scheme = re.search(r"^name:\s*(\S+)", yml, re.M).group(1)
    bundle_id = re.search(r"PRODUCT_BUNDLE_IDENTIFIER:\s*(\S+)", yml).group(1)

    versions = {}
    for label, cmd in (("xcode", ["xcodebuild", "-version"]), ("swift", ["xcrun", "swift", "--version"]),
                       ("xtool", ["xtool", "--version"])):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            versions[label] = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "?"
        except (OSError, subprocess.TimeoutExpired):
            versions[label] = "not installed"
    print(json.dumps(versions, indent=1), flush=True)

    subprocess.run(["xcodegen", "generate", "--spec", "project.yml"], cwd=str(app_dir), capture_output=True)
    tools = [XcodeBuild(app_dir, scheme), XcodeBuild(app_dir, scheme, sdk_only=True)]
    if shutil.which("xtool"):
        tools.append(XTool(make_xtool_copy(app_dir, scheme, bundle_id), scheme))
    samples = {t.name: {} for t in tools}

    def record(tool, step, secs):
        samples[tool.name].setdefault(step, []).append(secs)
        print(f"   {tool.name:22} {step:24} {fmt(secs)}", flush=True)

    # Runs are interleaved across tools so runner noise hits them alike.
    print("\n== first build (cold caches)", flush=True)
    for tool in tools:
        tool.clean()
        record(tool, "first build (cold caches)", tool.build())
    print("\n== clean builds", flush=True)
    for _ in range(max(1, args.runs - 1)):
        for tool in tools:
            tool.clean()
            record(tool, "clean build", tool.build())
    print("\n== no-op rebuilds", flush=True)
    for i in range(args.runs):
        for tool in tools:
            record(tool, "no-op rebuild", tool.build(timing=i == 0))
            if i == 0 and isinstance(tool, XcodeBuild):
                text = LAST_OUTPUT["text"]
                block = text[text.find("Build Timing Summary"):].strip().splitlines()[:25]
                print("      " + "\n      ".join(block), flush=True)
    print("\n== one-file edit rebuilds", flush=True)
    for _ in range(args.runs):
        edited = set()
        for tool in tools:
            if tool.root not in edited:
                toggle_edit(tool.root)
                edited.add(tool.root)
            record(tool, "one-file edit rebuild", tool.build())
    print("\n== install + launch", flush=True)
    for _ in range(args.runs):
        for tool in tools:
            t_i, t_l = install_and_launch(tool.app, tool.root, args.udid)
            record(tool, "install", t_i)
            record(tool, "launch until live", t_l)
    rows = {name: {step: median(v) for step, v in steps.items()} for name, steps in samples.items()}

    # For scale: UI changes through hot reload (no build), as the app reports them.
    hot = []
    if (app_dir / "ui").exists():
        install_and_launch(tools[0].app, app_dir, args.udid)
        time.sleep(2)
        for i in range(args.runs):
            op = json.dumps({"op": "set", "id": "home.hero.title", "props": {"text": f"Bench {i}"}})
            wall = run([sys.executable, "ui", "apply", op], app_dir)
            m = re.search(r"live on '[^']*' in ([0-9.]+)s", LAST_OUTPUT["text"])
            print(f"   ./ui apply: {fmt(wall)} wall, app reported {m.group(1) + 's' if m else 'no confirmation'}")
            hot.append(float(m.group(1)) if m else None)
        run([sys.executable, "ui", "undo", "--steps", str(args.runs)], app_dir)
    hot = median(hot)

    metrics = list(next(iter(rows.values())).keys())
    lines = [f"| step | {' | '.join(rows)} |", "|---|" + "---|" * len(rows)]
    for m in metrics:
        lines.append(f"| {m} | {' | '.join(fmt(rows[t].get(m)) for t in rows)} |")
    lines.append(f"\nHot-reload UI change (`./ui apply`, no build): {fmt(hot)}")
    lines.append(f"\nVersions: {versions}. Medians of {args.runs} runs (clean: {max(1, args.runs - 1)}).")
    table = "\n".join(lines)
    print("\n" + table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Build benchmark\n\n" + table + "\n")
    (app_dir / "bench.json").write_text(json.dumps({"rows": rows, "hot_reload": hot, "versions": versions}, indent=1))


if __name__ == "__main__":
    main()
