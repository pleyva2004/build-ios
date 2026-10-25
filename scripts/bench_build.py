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


def run(cmd, cwd, env=None, timeout=1800):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    secs = time.perf_counter() - t0
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
    name = "xcodebuild (run.sh)"

    def __init__(self, root: Path, scheme: str):
        self.root, self.scheme = root, scheme
        self.app = root / "build/Build/Products/Debug-iphonesimulator" / f"{scheme}.app"

    def clean(self):
        shutil.rmtree(self.root / "build", ignore_errors=True)

    def build(self):
        return run(["xcodebuild", "-project", f"{self.scheme}.xcodeproj", "-scheme", self.scheme,
                    "-configuration", "Debug", "-destination", "generic/platform=iOS Simulator",
                    "-derivedDataPath", "build", "ARCHS=arm64", "ONLY_ACTIVE_ARCH=YES",
                    "COMPILER_INDEX_STORE_ENABLE=NO", "build"], self.root)


class XTool:
    name = "xtool (SwiftPM)"

    def __init__(self, root: Path, scheme: str):
        self.root, self.scheme = root, scheme
        self.app = root / "xtool" / f"{scheme}.app"

    def clean(self):
        shutil.rmtree(self.root / ".build", ignore_errors=True)
        shutil.rmtree(self.root / "xtool", ignore_errors=True)

    def build(self):
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
    tools = [XcodeBuild(app_dir, scheme)]
    if shutil.which("xtool"):
        tools.append(XTool(make_xtool_copy(app_dir, scheme, bundle_id), scheme))

    rows = {}
    for tool in tools:
        print(f"\n== {tool.name}", flush=True)
        r = rows[tool.name] = {}
        tool.clean()
        r["first build (cold caches)"] = tool.build()
        cleans, noops, edits = [], [], []
        for i in range(max(1, args.runs - 1)):
            tool.clean()
            cleans.append(tool.build())
        for i in range(args.runs):
            noops.append(tool.build())
        for i in range(args.runs):
            toggle_edit(tool.root)
            edits.append(tool.build())
        r["clean build"] = median(cleans)
        r["no-op rebuild"] = median(noops)
        r["one-file edit rebuild"] = median(edits)
        installs, launches = [], []
        for i in range(args.runs):
            t_i, t_l = install_and_launch(tool.app, tool.root, args.udid)
            installs.append(t_i)
            launches.append(t_l)
        r["install"] = median(installs)
        r["launch until live"] = median(launches)
        for k, v in r.items():
            print(f"   {k:28} {fmt(v)}", flush=True)

    # For scale: a UI change through hot reload (the prototype is running from app_dir's last launch)
    hot = None
    if (app_dir / "ui").exists():
        install_and_launch(tools[0].app, app_dir, args.udid)
        hot = run([sys.executable, "ui", "apply",
                   '{"op":"set","id":"home.hero.title","props":{"text":"Bench"}}'], app_dir)
        run([sys.executable, "ui", "undo"], app_dir)

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
