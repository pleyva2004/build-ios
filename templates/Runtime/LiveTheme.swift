// LiveTheme.swift — resolves "$token" references and raw values (hex colors,
// font specs, spacing) into SwiftUI values. Part of the live-UI runtime.
import SwiftUI
import UIKit

struct LiveShadow {
    let color: Color
    let radius: CGFloat
    let x: CGFloat
    let y: CGFloat
}

struct LiveTheme {
    let tokens: LiveTokens
    let dark: Bool
    let scope: LiveScope

    // MARK: strings

    func text(_ v: Any?) -> String? {
        guard let s = LiveJSON.string(v) else { return nil }
        return LiveJSON.interpolate(s, scope)
    }

    /// "$name" -> "name", otherwise nil.
    func tokenName(_ s: String) -> String? {
        guard s.hasPrefix("$") else { return nil }
        return String(s.dropFirst())
    }

    // MARK: style presets

    /// Node props with its `style` bundle merged underneath (node wins).
    func effectiveProps(_ props: JSONObject) -> JSONObject {
        guard let raw = props["style"] as? String else { return props }
        let name = tokenName(raw) ?? raw
        guard let bundle = tokens.styles[name] as? JSONObject else { return props }
        var merged = bundle
        merged.removeValue(forKey: "style")
        for (k, v) in props where k != "style" {
            merged[k] = v
        }
        return merged
    }

    // MARK: colors

    func color(_ v: Any?) -> Color? {
        color(v, depth: 0)
    }

    private func color(_ v: Any?, depth: Int) -> Color? {
        guard depth < 6, let raw = text(v) else { return nil }
        let s = raw.trimmingCharacters(in: .whitespaces)
        if let name = tokenName(s) {
            if dark, let d = tokens.colorsDark[name] { return color(d, depth: depth + 1) }
            if let c = tokens.colors[name] { return color(c, depth: depth + 1) }
            return nil
        }
        if s.hasPrefix("#") { return LiveTheme.hex(s) }
        return LiveTheme.named(s)
    }

    static func hex(_ s: String) -> Color? {
        var h = String(s.dropFirst())
        if h.count == 3 {
            h = h.map { "\($0)\($0)" }.joined()
        }
        guard h.count == 6 || h.count == 8, let value = UInt64(h, radix: 16) else { return nil }
        let r, g, b, a: Double
        if h.count == 8 {
            r = Double((value >> 24) & 0xFF) / 255
            g = Double((value >> 16) & 0xFF) / 255
            b = Double((value >> 8) & 0xFF) / 255
            a = Double(value & 0xFF) / 255
        } else {
            r = Double((value >> 16) & 0xFF) / 255
            g = Double((value >> 8) & 0xFF) / 255
            b = Double(value & 0xFF) / 255
            a = 1
        }
        return Color(.sRGB, red: r, green: g, blue: b, opacity: a)
    }

    static func named(_ s: String) -> Color? {
        switch s {
        case "blue": return .blue
        case "red": return .red
        case "green": return .green
        case "orange": return .orange
        case "pink": return .pink
        case "purple": return .purple
        case "teal": return .teal
        case "indigo": return .indigo
        case "yellow": return .yellow
        case "mint": return .mint
        case "cyan": return .cyan
        case "brown": return .brown
        case "gray", "grey": return .gray
        case "black": return .black
        case "white": return .white
        case "clear": return .clear
        case "primary", "label": return .primary
        case "secondary", "secondaryLabel": return .secondary
        case "accent", "accentColor": return .accentColor
        case "systemBackground", "background": return Color(uiColor: .systemBackground)
        case "secondarySystemBackground": return Color(uiColor: .secondarySystemBackground)
        case "systemGroupedBackground", "groupedBackground": return Color(uiColor: .systemGroupedBackground)
        case "separator": return Color(uiColor: .separator)
        default: return nil
        }
    }

    /// Background fill: a color, or {"gradient": [c1, c2, ...], "direction": "down|right|diagonal"}.
    func fill(_ v: Any?) -> AnyShapeStyle? {
        if let g = v as? JSONObject, let stops = g["gradient"] as? [Any] {
            let colors = stops.compactMap { color($0) }
            guard colors.count >= 2 else { return colors.first.map { AnyShapeStyle($0) } }
            let start: UnitPoint
            let end: UnitPoint
            switch text(g["direction"]) ?? "down" {
            case "right":
                start = .leading
                end = .trailing
            case "diagonal":
                start = .topLeading
                end = .bottomTrailing
            case "up":
                start = .bottom
                end = .top
            default:
                start = .top
                end = .bottom
            }
            return AnyShapeStyle(LinearGradient(colors: colors, startPoint: start, endPoint: end))
        }
        if let c = color(v) { return AnyShapeStyle(c) }
        return nil
    }

    // MARK: numbers

    /// Number or "$token" (looked up in `group`, then spacing, then radius). "infinity"
    /// is only accepted where `allowInfinity` (frame sizes); NaN and huge values are
    /// rejected so a bad value can never crash layout.
    func number(_ v: Any?, group: JSONObject? = nil, allowInfinity: Bool = false) -> CGFloat? {
        number(v, group: group, allowInfinity: allowInfinity, depth: 0)
    }

    private func number(_ v: Any?, group: JSONObject?, allowInfinity: Bool, depth: Int) -> CGFloat? {
        guard depth < 6, let s = text(v) else { return nil } // depth guard: "$a" -> "$a" can't recurse forever
        let t = s.trimmingCharacters(in: .whitespaces)
        if let name = tokenName(t) {
            if let g = group, let found = g[name] {
                return number(found, group: nil, allowInfinity: allowInfinity, depth: depth + 1)
            }
            if let found = tokens.spacing[name] {
                return number(found, group: nil, allowInfinity: allowInfinity, depth: depth + 1)
            }
            if let found = tokens.radius[name] {
                return number(found, group: nil, allowInfinity: allowInfinity, depth: depth + 1)
            }
            return nil
        }
        if t == "infinity" || t == "inf" || t == "max" {
            return allowInfinity ? .infinity : nil
        }
        guard let d = Double(t), d.isFinite, abs(d) <= 100_000 else { return nil }
        return CGFloat(d)
    }

    /// Frame sizes: numbers, tokens, or "infinity".
    func frameNumber(_ v: Any?) -> CGFloat? { number(v, allowInfinity: true) }

    func spacing(_ v: Any?) -> CGFloat? { number(v, group: tokens.spacing) }

    func radius(_ v: Any?) -> CGFloat? { number(v, group: tokens.radius) }

    /// padding: n | "$token" | {"top","bottom","leading","trailing","horizontal","vertical"}
    func insets(_ v: Any?) -> EdgeInsets? {
        if let n = spacing(v) {
            return EdgeInsets(top: n, leading: n, bottom: n, trailing: n)
        }
        guard let d = v as? JSONObject else { return nil }
        let all = spacing(d["all"]) ?? 0
        let h = spacing(d["horizontal"]) ?? all
        let vv = spacing(d["vertical"]) ?? all
        return EdgeInsets(top: spacing(d["top"]) ?? vv,
                          leading: spacing(d["leading"]) ?? h,
                          bottom: spacing(d["bottom"]) ?? vv,
                          trailing: spacing(d["trailing"]) ?? h)
    }

    // MARK: shadows

    func shadow(_ v: Any?) -> LiveShadow? {
        let spec: JSONObject?
        if let s = text(v) {
            if s == "none" { return nil }
            let name = tokenName(s) ?? s
            spec = tokens.shadows[name] as? JSONObject
        } else {
            spec = v as? JSONObject
        }
        guard let d = spec else { return nil }
        let opacity = LiveJSON.double(d["opacity"]) ?? 0.15
        let base = color(d["color"]) ?? .black
        return LiveShadow(color: base.opacity(opacity),
                          radius: number(d["radius"]) ?? 8,
                          x: number(d["x"]) ?? 0,
                          y: number(d["y"]) ?? 2)
    }

    // MARK: fonts

    static func weight(_ s: String?) -> Font.Weight? {
        switch s {
        case "ultraLight": return .ultraLight
        case "thin": return .thin
        case "light": return .light
        case "regular": return .regular
        case "medium": return .medium
        case "semibold": return .semibold
        case "bold": return .bold
        case "heavy": return .heavy
        case "black": return .black
        default: return nil
        }
    }

    static func design(_ s: String?) -> Font.Design? {
        switch s {
        case "default": return .default
        case "rounded": return .rounded
        case "serif": return .serif
        case "monospaced", "mono": return .monospaced
        default: return nil
        }
    }

    static func textStyle(_ s: String?) -> Font.TextStyle? {
        switch s {
        case "largeTitle": return .largeTitle
        case "title": return .title
        case "title2": return .title2
        case "title3": return .title3
        case "headline": return .headline
        case "subheadline": return .subheadline
        case "body": return .body
        case "callout": return .callout
        case "footnote": return .footnote
        case "caption": return .caption
        case "caption2": return .caption2
        default: return nil
        }
    }

    /// Resolves `font` (token, text style name, or {size, weight, design, style})
    /// plus node-level `size` / `weight` / `design` overrides. Returns nil when the
    /// node sets only weight/design (those are applied as separate modifiers so the
    /// inherited font keeps its size).
    func font(_ props: JSONObject) -> Font? {
        var size: CGFloat? = nil
        var weightName: String? = nil
        var designName: String? = nil
        var styleName: String? = nil

        var spec: Any? = props["font"]
        var depth = 0
        while let s = text(spec), depth < 4 {
            depth += 1
            if let name = tokenName(s) {
                spec = tokens.fonts[name]
            } else {
                styleName = s
                spec = nil
            }
        }
        if let d = spec as? JSONObject {
            size = number(d["size"])
            weightName = text(d["weight"])
            designName = text(d["design"])
            if let st = text(d["style"]) { styleName = st }
        }
        if let s = number(props["size"]) { size = s }
        if let w = text(props["weight"]) { weightName = w }
        if let d = text(props["design"]) { designName = d }

        let weight = LiveTheme.weight(weightName)
        let design = LiveTheme.design(designName)
        if let size = size {
            let clamped = min(max(size, 1), 400)
            return .system(size: clamped, weight: weight, design: design)
        }
        if let style = LiveTheme.textStyle(styleName) {
            return .system(style, design: design, weight: weight)
        }
        return nil
    }
}
