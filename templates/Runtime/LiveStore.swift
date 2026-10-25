// LiveStore.swift — owns the loaded design, demo state and navigation, and
// hot-reloads design/*.json from the host Mac while running in the Simulator.
//
// Dev channel (<project>/.live/, only when the project folder is reachable):
//   app writes   status.json     current screen, nav stack, applied rev, errors
//                selection.json  element tapped in pick mode
//   tools write  rev             {"rev": N, "changed": [ids]} after each ./ui apply
//                pick            presence = enter pick mode
import Combine
import Foundation
import SwiftUI
import UIKit

struct LiveRoute: Hashable, Identifiable {
    let id = UUID()
    let screen: String
    let params: [String: String]
}

final class LiveStore: ObservableObject {
    @Published private(set) var spec: LiveSpec = .empty
    @Published private(set) var specVersion: Int = 0
    @Published var state: JSONObject = [:]
    @Published var selectedTab: String = "" { didSet { statusDirty() } }
    @Published var paths: [String: [LiveRoute]] = [:] { didSet { statusDirty() } }
    @Published var sheet: LiveRoute? = nil { didSet { statusDirty() } }
    @Published var sheetPath: [LiveRoute] = [] { didSet { statusDirty() } }
    @Published var alertShown: Bool = false
    @Published var alertTitle: String = ""
    @Published var alertMessage: String = ""
    @Published private(set) var error: String? = nil
    @Published private(set) var picking: Bool = false
    @Published private(set) var selectedId: String? = nil
    @Published private(set) var flashIds: Set<String> = []

    /// nil when running from the bundled copy (no hot reload).
    let projectDir: URL?
    let designDir: URL
    /// How the design folder was found: env | defaults | source | bundle.
    let source: String

    private var appliedRev: Int = 0
    private var lastSignature: String = ""
    private var failedSignature: String = ""
    private var lastInitialState: JSONObject = [:]
    private var timer: DispatchSourceTimer?
    private let io = DispatchQueue(label: "live.design.io")
    private var statusScheduled = false
    private var pickHandledAt: TimeInterval = 0
    private let launchedAt = Date().timeIntervalSince1970

    var isLive: Bool { projectDir != nil }

    /// In Debug Simulator builds, say so when hot reload is off (bundled design).
    var showNotLiveHint: Bool {
        #if DEBUG && targetEnvironment(simulator)
        return !isLive
        #else
        return false
        #endif
    }

    /// Alerts are shown by the sheet while one is presented, else by the root.
    func alertBinding(inSheet: Bool) -> Binding<Bool> {
        Binding(get: { self.alertShown && ((self.sheet != nil) == inSheet) },
                set: { self.alertShown = $0 })
    }

    init() {
        let found = LiveStore.locateDesign()
        projectDir = found.project
        designDir = found.design
        source = found.source
        if found.project != nil {
            // Fingerprint before loading so an edit landing during launch is
            // picked up by the first tick.
            lastSignature = signature()
            appliedRev = readRev().rev
        }
        do {
            let loaded = try LiveSpec.load(from: designDir)
            spec = loaded
        } catch let e as LiveError {
            error = e.message
        } catch {
            self.error = error.localizedDescription
        }
        state = spec.initialState
        lastInitialState = spec.initialState
        pickHandledAt = launchedAt // ignore a stale .live/pick from an earlier run
        selectedTab = spec.tabs.first?.id ?? "_root"
        if isLive {
            startWatching()
            statusDirty()
        }
    }

    deinit {
        timer?.cancel()
    }

    // MARK: locating the design folder

    /// Order: $UI_PROJECT_DIR (run.sh passes SIMCTL_CHILD_UI_PROJECT_DIR), then the
    /// UIProjectDir default (run.sh passes `-UIProjectDir <path>` as a launch
    /// argument), then the folder this file was compiled from (Debug builds; keeps a
    /// home-screen relaunch live), then the copy bundled into the app (not live).
    static func locateDesign() -> (project: URL?, design: URL, source: String) {
        let fm = FileManager.default
        var candidates: [(String, String)] = []
        if let env = ProcessInfo.processInfo.environment["UI_PROJECT_DIR"], !env.isEmpty {
            candidates.append((env, "env"))
        }
        if let saved = UserDefaults.standard.string(forKey: "UIProjectDir"), !saved.isEmpty {
            candidates.append((saved, "defaults"))
        }
        #if DEBUG
        let file = URL(fileURLWithPath: #filePath) // <project>/Sources/Runtime/LiveStore.swift
        candidates.append((file.deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().path, "source"))
        #endif
        for (path, how) in candidates {
            let project = URL(fileURLWithPath: path)
            let design = project.appendingPathComponent("design")
            if fm.isReadableFile(atPath: design.appendingPathComponent("app.json").path) {
                return (project, design, how)
            }
        }
        let bundled = Bundle.main.url(forResource: "design", withExtension: nil)
            ?? Bundle.main.bundleURL.appendingPathComponent("design")
        return (nil, bundled, "bundle")
    }

    var liveDir: URL? { projectDir?.appendingPathComponent(".live") }

    // MARK: hot reload (polls mtimes off the main thread)

    private func startWatching() {
        let t = DispatchSource.makeTimerSource(queue: io)
        t.schedule(deadline: .now() + 0.15, repeating: 0.15, leeway: .milliseconds(20))
        t.setEventHandler { [weak self] in self?.tick() }
        t.resume()
        timer = t
    }

    private func tick() {
        let sig = signature()
        if sig != lastSignature {
            lastSignature = sig
            reload()
        }
        checkPickRequest()
    }

    /// Fingerprint of every design file plus .live/rev (path, mtime, size).
    private func signature() -> String {
        let fm = FileManager.default
        var parts: [String] = []
        var files: [String] = []
        for dir in [designDir.path, designDir.appendingPathComponent("screens").path] {
            let names = ((try? fm.contentsOfDirectory(atPath: dir)) ?? [])
                .filter { $0.hasSuffix(".json") && !$0.hasPrefix(".") }
            for name in names.sorted() { files.append(dir + "/" + name) }
        }
        for path in files {
            guard let attrs = try? fm.attributesOfItem(atPath: path) else { continue }
            let mtime = (attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
            let size = (attrs[.size] as? NSNumber)?.intValue ?? 0
            parts.append("\(path):\(mtime):\(size)")
        }
        // The rev file's contents are part of the fingerprint, so a ./ui apply is
        // never missed even on file systems with coarse mtimes.
        if let live = liveDir,
           let rev = try? String(contentsOf: live.appendingPathComponent("rev"), encoding: .utf8) {
            parts.append(rev)
        }
        return parts.joined(separator: "|")
    }

    /// .live/rev: {"rev": N, "changed": [ids], "quiet": bool}. "quiet" (screenshots)
    /// suppresses the orange change outline.
    private func readRev() -> (rev: Int, changed: [String]) {
        guard let url = liveDir?.appendingPathComponent("rev"),
              let data = try? Data(contentsOf: url),
              let obj = (try? JSONSerialization.jsonObject(with: data, options: [])) as? JSONObject
        else { return (0, []) }
        let quiet = LiveJSON.bool(obj["quiet"]) ?? false
        let changed = quiet ? [] : ((obj["changed"] as? [Any])?.compactMap { $0 as? String } ?? [])
        return (LiveJSON.int(obj["rev"]) ?? 0, changed)
    }

    private func reload() {
        let rev = readRev()
        do {
            let next = try LiveSpec.load(from: designDir)
            failedSignature = ""
            DispatchQueue.main.async {
                self.apply(next, rev: rev.rev, changed: rev.changed)
            }
        } catch {
            // A hand edit may be caught half-written: retry once on the next tick
            // before showing an error banner.
            if failedSignature != lastSignature {
                failedSignature = lastSignature
                lastSignature = ""
                return
            }
            let message = (error as? LiveError)?.message ?? error.localizedDescription
            DispatchQueue.main.async {
                self.error = message
                self.appliedRev = rev.rev
                self.statusDirty()
            }
        }
    }

    private func apply(_ next: LiveSpec, rev: Int, changed: [String]) {
        spec = next
        specVersion += 1
        error = nil
        mergeInitialState(next.initialState)
        if next.tabs.isEmpty {
            if selectedTab != "_root" { selectedTab = "_root" }
        } else if !next.tabs.contains(where: { $0.id == selectedTab }) {
            selectedTab = next.tabs[0].id
        }
        appliedRev = rev
        flash(changed)
        statusDirty()
    }

    /// New state keys are added; keys whose initial value changed in app.json
    /// take the new value; values the user changed by tapping are otherwise kept.
    private func mergeInitialState(_ initial: JSONObject) {
        var next = state
        for (key, value) in initial {
            let old = lastInitialState[key] as? NSObject
            let unchanged = old?.isEqual(value) ?? false
            if next[key] == nil || !unchanged {
                next[key] = value
            }
        }
        lastInitialState = initial
        state = next
    }

    private func flash(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        let set = Set(ids)
        flashIds = set
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
            if self.flashIds == set { self.flashIds = [] }
        }
    }

    // MARK: pick mode

    /// A pick request is a `.live/pick` file newer than the last handled pick.
    private func checkPickRequest() {
        guard let url = liveDir?.appendingPathComponent("pick") else { return }
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let mtime = (attrs?[.modificationDate] as? Date)?.timeIntervalSince1970
        DispatchQueue.main.async {
            let requested = mtime.map { $0 > self.pickHandledAt } ?? false
            if requested != self.picking {
                self.picking = requested
                self.statusDirty()
            }
        }
    }

    /// `row` is the repeat row index when the element is inside a repeat template.
    func select(id: String, type: String, instance: String, row: Int?) {
        selectedId = id
        picking = false
        pickHandledAt = Date().timeIntervalSince1970
        flash([id])
        let screen = currentScreen
        guard let live = liveDir else { return }
        try? FileManager.default.removeItem(at: live.appendingPathComponent("pick"))
        var payload: JSONObject = [
            "id": id,
            "type": type,
            "instance": instance,
            "screen": screen,
            "at": Date().timeIntervalSince1970,
        ]
        if let r = row { payload["row"] = r }
        io.async {
            self.writeJSON(payload, to: live.appendingPathComponent("selection.json"))
        }
        statusDirty()
    }

    // MARK: state

    func bool(_ key: String) -> Bool {
        LiveJSON.bool(state[key]) ?? false
    }

    func string(_ key: String) -> String {
        LiveJSON.string(state[key]) ?? ""
    }

    func setState(_ key: String, _ value: Any) {
        state[key] = value
    }

    /// visibleIf: "key" | "!key" | "key==value" | "key!=value". Also works after
    /// {{interpolation}} turned the key into a literal ("true", "done==done").
    func isVisible(_ condition: String) -> Bool {
        let c = condition.trimmingCharacters(in: .whitespaces)
        for op in ["!=", "=="] {
            if let r = c.range(of: op) {
                let lhs = c[c.startIndex..<r.lowerBound].trimmingCharacters(in: .whitespaces)
                let rhs = c[r.upperBound...].trimmingCharacters(in: .whitespaces)
                let value = state[lhs] != nil ? string(lhs) : lhs
                return op == "==" ? value == rhs : value != rhs
            }
        }
        let negate = c.hasPrefix("!")
        let key = negate ? String(c.dropFirst()) : c
        let result: Bool
        switch key.lowercased() {
        case "true", "yes", "1": result = true
        case "false", "no", "0", "": result = false
        default: result = bool(key)
        }
        return negate ? !result : result
    }

    // MARK: navigation + actions

    var currentScreen: String {
        if let s = sheet { return sheetPath.last?.screen ?? s.screen }
        if let last = paths[selectedTab]?.last { return last.screen }
        if let tab = spec.tabs.first(where: { $0.id == selectedTab }) { return tab.screen }
        return spec.rootScreen
    }

    func pathBinding(_ tab: String) -> Binding<[LiveRoute]> {
        Binding(get: { self.paths[tab] ?? [] },
                set: { self.paths[tab] = $0 })
    }

    var sheetPathBinding: Binding<[LiveRoute]> {
        Binding(get: { self.sheetPath }, set: { self.sheetPath = $0 })
    }

    func perform(_ action: Any?, scope: LiveScope) {
        if let list = action as? [Any] {
            for item in list { perform(item, scope: scope) }
            return
        }
        guard let a = action as? JSONObject else { return }
        let theme = LiveTheme(tokens: spec.tokens, dark: false, scope: scope)
        if let target = theme.text(a["navigate"]) {
            let route = LiveRoute(screen: target, params: params(a["params"], theme))
            if sheet != nil {
                sheetPath.append(route)
            } else {
                var p = paths[selectedTab] ?? []
                p.append(route)
                paths[selectedTab] = p
            }
        } else if let target = theme.text(a["sheet"]) {
            sheetPath = []
            sheet = LiveRoute(screen: target, params: params(a["params"], theme))
        } else if a["dismiss"] != nil {
            dismiss()
        } else if let tab = theme.text(a["tab"]) {
            selectedTab = tab
        } else if let key = theme.text(a["toggle"]) {
            state[key] = !bool(key)
        } else if let values = a["set"] as? JSONObject {
            for (key, value) in values {
                if let s = value as? String {
                    state[key] = LiveJSON.interpolate(s, scope)
                } else {
                    state[key] = value
                }
            }
        } else if let link = theme.text(a["url"]), let url = URL(string: link) {
            DispatchQueue.main.async {
                UIApplication.shared.open(url, options: [:], completionHandler: nil)
            }
        } else if let alert = a["alert"] as? JSONObject {
            alertTitle = theme.text(alert["title"]) ?? ""
            alertMessage = theme.text(alert["message"]) ?? ""
            alertShown = true
        }
    }

    private func params(_ v: Any?, _ theme: LiveTheme) -> [String: String] {
        var out: [String: String] = [:]
        for (key, value) in LiveJSON.object(v) {
            out[key] = theme.text(value) ?? ""
        }
        return out
    }

    func dismiss() {
        if sheet != nil {
            if sheetPath.isEmpty {
                sheet = nil
            } else {
                sheetPath.removeLast()
            }
        } else if var p = paths[selectedTab], !p.isEmpty {
            p.removeLast()
            paths[selectedTab] = p
        }
    }

    // MARK: status channel

    /// Coalesces status writes (navigation can change several properties at once).
    private func statusDirty() {
        guard isLive, !statusScheduled else { return }
        statusScheduled = true
        DispatchQueue.main.async {
            self.statusScheduled = false
            self.writeStatus()
        }
    }

    private func writeStatus() {
        guard let live = liveDir else { return }
        var stack: [String] = []
        if let tab = spec.tabs.first(where: { $0.id == selectedTab }) {
            stack.append(tab.screen)
        } else if spec.tabs.isEmpty {
            stack.append(spec.rootScreen)
        }
        stack.append(contentsOf: (paths[selectedTab] ?? []).map { $0.screen })
        if let s = sheet {
            stack.append(s.screen)
            stack.append(contentsOf: sheetPath.map { $0.screen })
        }
        var payload: JSONObject = [
            "rev": appliedRev,
            "screen": currentScreen,
            "stack": stack,
            "tab": selectedTab,
            "picking": picking,
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "launchedAt": launchedAt,
            "updatedAt": Date().timeIntervalSince1970,
            "source": source,
            "designDir": designDir.path,
            "components": LiveComponents.catalog,
        ]
        if let e = error { payload["error"] = e }
        if let s = selectedId { payload["selected"] = s }
        io.async {
            self.writeJSON(payload, to: live.appendingPathComponent("status.json"))
        }
    }

    private func writeJSON(_ obj: JSONObject, to url: URL) {
        let fm = FileManager.default
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard JSONSerialization.isValidJSONObject(obj),
              let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
        else { return }
        try? data.write(to: url, options: .atomic)
    }
}
