// Components.swift — native SwiftUI views you can place from design JSON with
// {"type": "custom", "name": "<Name>", ...props, "children": [...]}.
//
// This is the escape hatch for anything the JSON nodes can't express (charts,
// maps, gestures, special animations). Adding or changing a component needs one
// rebuild (./run.sh); after that, placing it and changing its props are
// hot-reloadable like everything else.
//
// Contract: one file per component in Sources/Components/, plus one line in
// `registry` and one in `catalog` (the catalog is what ./ui and models see).
import SwiftUI

/// Props handed to a component, already resolved against tokens and {{scope}}.
struct LiveProps {
    let raw: JSONObject
    let theme: LiveTheme
    let store: LiveStore
    let scope: LiveScope
    /// The node's "children", already rendered (lets a component act as a container).
    let children: [AnyView]

    func string(_ key: String) -> String? { theme.text(raw[key]) }
    func number(_ key: String) -> Double? { theme.number(raw[key]).map { Double($0) } }
    func int(_ key: String) -> Int? {
        guard let d = number(key), d.isFinite, abs(d) < 1_000_000 else { return nil }
        return Int(d)
    }
    func bool(_ key: String) -> Bool? { LiveJSON.bool(theme.text(raw[key])) }
    func color(_ key: String) -> Color? { theme.color(raw[key]) }

    /// Runs the action stored in `key` (same format as a button's "action").
    func action(_ key: String = "action") -> () -> Void {
        let store = self.store
        let scope = self.scope
        let value = raw[key]
        return { store.perform(value, scope: scope) }
    }

    /// Two-way binding to the demo state key named by the prop `key` (e.g. "bind").
    func boolBinding(_ key: String = "bind") -> Binding<Bool> {
        let store = self.store
        let name = string(key) ?? key
        return Binding(get: { store.bool(name) }, set: { store.setState(name, $0) })
    }

    func stringBinding(_ key: String = "bind") -> Binding<String> {
        let store = self.store
        let name = string(key) ?? key
        return Binding(get: { store.string(name) }, set: { store.setState(name, $0) })
    }
}

enum LiveComponents {
    /// Name used in JSON -> view factory.
    static let registry: [String: (LiveProps) -> AnyView] = [
        "RatingStars": { props in
            AnyView(RatingStars(rating: props.number("rating") ?? 4,
                                outOf: props.int("outOf") ?? 5,
                                color: props.color("color") ?? .orange))
        },
    ]

    /// Name -> one-line props doc. Reported to ./ui (status.json) so models can use them.
    static let catalog: [String: String] = [
        "RatingStars": "rating (0-5, halves ok), outOf (int, default 5), color",
    ]
}

/// Example component: {"type": "custom", "name": "RatingStars", "rating": 4.5}
struct RatingStars: View {
    let rating: Double
    let outOf: Int
    let color: Color

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<min(max(outOf, 0), 20), id: \.self) { index in
                Image(systemName: symbol(for: index))
                    .foregroundStyle(color)
            }
        }
    }

    func symbol(for index: Int) -> String {
        let value = rating - Double(index)
        if value >= 1 { return "star.fill" }
        if value >= 0.5 { return "star.leadinghalf.filled" }
        return "star"
    }
}
