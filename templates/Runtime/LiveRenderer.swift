// LiveRenderer.swift — turns a LiveNode tree into SwiftUI views. Every helper
// returns AnyView so the type-checker never sees one huge generic expression.
// Part of the live-UI runtime (see tools/REFERENCE.md for the JSON format).
import SwiftUI
import UIKit

struct LiveRenderItem: Identifiable {
    let id: String
    let node: LiveNode
    let scope: LiveScope
}

struct LiveNodeView: View {
    let node: LiveNode
    let scope: LiveScope
    let key: String
    @EnvironmentObject var store: LiveStore
    @Environment(\.colorScheme) var colorScheme

    var body: some View {
        render()
    }
}

extension LiveNodeView {
    static let containerTypes: Set<String> = ["vstack", "hstack", "zstack", "scroll", "grid", "list", "section", "repeat"]
    static let buttonVariants: Set<String> = ["primary", "secondary", "plain", "borderless", "destructive", "glass", "glassProminent"]
    /// Variants drawn by a bordered system style (they take tint + border shape).
    static let systemButtonVariants: Set<String> = ["primary", "secondary", "glass", "glassProminent"]

    func makeTheme(_ scope: LiveScope) -> LiveTheme {
        LiveTheme(tokens: store.spec.tokens, dark: colorScheme == .dark, scope: scope)
    }

    func render() -> AnyView {
        let theme = makeTheme(scope)
        let p = theme.effectiveProps(node.props)
        if let condition = theme.text(p["visibleIf"]), !store.isVisible(condition) {
            return AnyView(EmptyView())
        }
        if node.type == "button" {
            return renderButton(p, theme)
        }
        var view = content(p, theme)
        view = textModifiers(view, p, theme)
        view = layoutModifiers(view, p, theme)
        view = decorationModifiers(view, p, theme)
        let isLeaf = !LiveNodeView.containerTypes.contains(node.type)
        view = behaviorModifiers(view, p, theme, isLeaf: isLeaf, allowTap: true)
        return view
    }

    // MARK: children

    func child(_ item: LiveRenderItem) -> LiveNodeView {
        LiveNodeView(node: item.node, scope: item.scope, key: item.id)
    }

    /// Expands `repeat` nodes into one item per data element and gives every
    /// item a unique key (ForEach needs unique ids).
    func expand(_ children: [LiveNode], in scope: LiveScope) -> [LiveRenderItem] {
        var out: [LiveRenderItem] = []
        var seen = Set<String>()
        for child in children {
            if child.type == "repeat" {
                guard isShown(child, in: scope), let template = child.template else { continue }
                let items = repeatItems(child, scope)
                for (i, element) in items.enumerated() {
                    var s = scope
                    if let obj = element as? JSONObject {
                        s.item = obj
                    } else {
                        s.item = ["value": element]
                    }
                    s.index = i
                    guard isShown(template, in: s) else { continue }
                    let key = LiveNodeView.unique("\(child.id)#\(i)", &seen)
                    out.append(LiveRenderItem(id: key, node: template, scope: s))
                }
            } else {
                guard isShown(child, in: scope) else { continue }
                let key = LiveNodeView.unique(child.id, &seen)
                out.append(LiveRenderItem(id: key, node: child, scope: scope))
            }
        }
        return out
    }

    /// Evaluates `visibleIf` (also from a style bundle) so hidden children are
    /// dropped from the list instead of taking up stack spacing.
    func isShown(_ node: LiveNode, in scope: LiveScope) -> Bool {
        let theme = makeTheme(scope)
        let props = theme.effectiveProps(node.props)
        guard let condition = theme.text(props["visibleIf"]) else { return true }
        return store.isVisible(condition)
    }

    static func unique(_ id: String, _ seen: inout Set<String>) -> String {
        var candidate = id
        var n = 1
        while seen.contains(candidate) {
            n += 1
            candidate = "\(id)~\(n)"
        }
        seen.insert(candidate)
        return candidate
    }

    /// `items`: inline array, a count, or a path like "data.stories".
    func repeatItems(_ node: LiveNode, _ scope: LiveScope) -> [Any] {
        let raw = node.props["items"]
        if let list = raw as? [Any] { return Array(list.prefix(500)) }
        if let n = LiveJSON.int(raw), !(raw is String) {
            return (0..<min(max(0, n), 500)).map { $0 as Any }
        }
        if let s = raw as? String {
            var path = s.replacingOccurrences(of: "{{", with: "").replacingOccurrences(of: "}}", with: "")
            if path.hasPrefix("$") { path = String(path.dropFirst()) }
            path = path.trimmingCharacters(in: .whitespaces)
            if let list = scope.value(path) as? [Any] { return Array(list.prefix(500)) }
            if let n = Int(path) { return (0..<min(max(0, n), 500)).map { $0 as Any } }
        }
        return []
    }

    // MARK: alignment helpers

    static func hAlign(_ s: String?) -> HorizontalAlignment {
        switch s {
        case "leading": return .leading
        case "trailing": return .trailing
        default: return .center
        }
    }

    static func vAlign(_ s: String?) -> VerticalAlignment {
        switch s {
        case "top": return .top
        case "bottom": return .bottom
        case "firstTextBaseline", "baseline": return .firstTextBaseline
        default: return .center
        }
    }

    static func alignment(_ s: String?) -> Alignment? {
        switch s {
        case "center": return .center
        case "top": return .top
        case "bottom": return .bottom
        case "leading": return .leading
        case "trailing": return .trailing
        case "topLeading": return .topLeading
        case "topTrailing": return .topTrailing
        case "bottomLeading": return .bottomLeading
        case "bottomTrailing": return .bottomTrailing
        default: return nil
        }
    }

    // MARK: content by type

    func content(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        switch node.type {
        case "vstack": return vstack(p, theme)
        case "hstack": return hstack(p, theme)
        case "zstack": return zstack(p, theme)
        case "scroll": return scroll(p, theme)
        case "grid": return grid(p, theme)
        case "list": return list(p, theme)
        case "section": return looseSection(p, theme)
        case "repeat": return repeatColumn(p, theme)
        case "text": return AnyView(Text(theme.text(p["text"]) ?? ""))
        case "label": return label(p, theme)
        case "image": return image(p, theme)
        case "toggle": return toggle(p, theme)
        case "textfield": return textField(p, theme)
        case "picker": return picker(p, theme)
        case "shape": return shape(p, theme)
        case "progress": return progress(p, theme)
        case "spacer": return AnyView(Spacer(minLength: theme.number(p["minLength"])))
        case "divider": return AnyView(Divider())
        case "custom": return custom(p, theme)
        default: return LiveNodeView.placeholder("unknown type “\(node.type)” (\(node.id))")
        }
    }

    func vstack(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let align = LiveNodeView.hAlign(theme.text(p["align"]))
        let spacing = theme.spacing(p["spacing"])
        return AnyView(VStack(alignment: align, spacing: spacing) {
            ForEach(items) { item in self.child(item) }
        })
    }

    func hstack(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let align = LiveNodeView.vAlign(theme.text(p["align"]))
        let spacing = theme.spacing(p["spacing"])
        return AnyView(HStack(alignment: align, spacing: spacing) {
            ForEach(items) { item in self.child(item) }
        })
    }

    func zstack(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let align = LiveNodeView.alignment(theme.text(p["align"])) ?? .center
        return AnyView(ZStack(alignment: align) {
            ForEach(items) { item in self.child(item) }
        })
    }

    /// Padding on a scroll node insets its content, not the scroll view.
    func scroll(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let spacing = theme.spacing(p["spacing"])
        let insets = theme.insets(p["padding"]) ?? EdgeInsets()
        if theme.text(p["axis"]) == "horizontal" {
            let align = LiveNodeView.vAlign(theme.text(p["align"]))
            return AnyView(ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: align, spacing: spacing) {
                    ForEach(items) { item in self.child(item) }
                }
                .padding(insets)
            })
        }
        let align = LiveNodeView.hAlign(theme.text(p["align"]))
        let frameAlign = Alignment(horizontal: align, vertical: .top)
        return AnyView(ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: align, spacing: spacing) {
                ForEach(items) { item in self.child(item) }
            }
            .padding(insets)
            .frame(maxWidth: .infinity, alignment: frameAlign)
        })
    }

    func grid(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let count = min(max(1, LiveJSON.int(theme.text(p["columns"])) ?? 2), 12)
        let spacing = theme.spacing(p["spacing"]) ?? 8
        let columns = Array(repeating: GridItem(.flexible(), spacing: spacing), count: count)
        return AnyView(LazyVGrid(columns: columns, spacing: spacing) {
            ForEach(items) { item in self.child(item) }
        })
    }

    /// Sections are built directly inside the List (not behind AnyView) so the
    /// List recognizes them and draws grouped headers/footers.
    func list(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand(node.children, in: scope)
        let base = List {
            ForEach(items) { item in
                if item.node.type == "section" {
                    self.listSection(item)
                } else {
                    self.child(item)
                }
            }
        }
        let background: Visibility = p["background"] == nil ? .automatic : .hidden
        switch theme.text(p["listStyle"]) ?? "insetGrouped" {
        case "plain":
            return AnyView(base.listStyle(.plain).scrollContentBackground(background))
        case "grouped":
            return AnyView(base.listStyle(.grouped).scrollContentBackground(background))
        case "inset":
            return AnyView(base.listStyle(.inset).scrollContentBackground(background))
        default:
            return AnyView(base.listStyle(.insetGrouped).scrollContentBackground(background))
        }
    }

    func listSection(_ item: LiveRenderItem) -> some View {
        let theme = makeTheme(item.scope)
        let p = theme.effectiveProps(item.node.props)
        let rows = expand(item.node.children, in: item.scope)
        let header = theme.text(p["header"])
        let footer = theme.text(p["footer"])
        return Section {
            ForEach(rows) { row in self.child(row) }
        } header: {
            if let h = header { Text(h) }
        } footer: {
            if let f = footer { Text(f) }
        }
    }

    /// A section used outside a list renders as a titled column.
    func looseSection(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let rows = expand(node.children, in: scope)
        let header = theme.text(p["header"])
        let footer = theme.text(p["footer"])
        return AnyView(VStack(alignment: .leading, spacing: 8) {
            if let h = header {
                Text(h).font(.footnote).foregroundStyle(.secondary).textCase(.uppercase)
            }
            ForEach(rows) { row in self.child(row) }
            if let f = footer {
                Text(f).font(.footnote).foregroundStyle(.secondary)
            }
        })
    }

    /// A repeat that is not inside a container renders as a column.
    func repeatColumn(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let items = expand([node], in: scope)
        let spacing = theme.spacing(p["spacing"])
        return AnyView(VStack(spacing: spacing) {
            ForEach(items) { item in self.child(item) }
        })
    }

    func label(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let title = theme.text(p["text"]) ?? ""
        if let symbol = theme.text(p["symbol"]) {
            return AnyView(Label(title, systemImage: symbol))
        }
        return AnyView(Text(title))
    }

    func image(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        if let symbol = theme.text(p["symbol"]) {
            // size / weight / design come from textModifiers (theme.font), so they combine.
            var view = AnyView(Image(systemName: symbol))
            switch theme.text(p["symbolMode"]) {
            case "hierarchical": view = AnyView(view.symbolRenderingMode(.hierarchical))
            case "multicolor": view = AnyView(view.symbolRenderingMode(.multicolor))
            case "monochrome": view = AnyView(view.symbolRenderingMode(.monochrome))
            default: break
            }
            return view
        }
        let mode: ContentMode = theme.text(p["fit"]) == "fit" ? .fit : .fill
        let ratio = theme.number(p["aspect"])
        if let name = theme.text(p["asset"]) {
            return AnyView(Image(name).resizable().aspectRatio(ratio, contentMode: mode))
        }
        if let link = theme.text(p["url"]) {
            return AnyView(AsyncImage(url: URL(string: link)) { phase in
                if let loaded = phase.image {
                    loaded.resizable().aspectRatio(ratio, contentMode: mode)
                } else if phase.error != nil {
                    ZStack {
                        Color.gray.opacity(0.2)
                        Image(systemName: "photo").foregroundStyle(.secondary)
                    }
                } else {
                    Color.gray.opacity(0.12)
                }
            })
        }
        return AnyView(Image(systemName: "photo"))
    }

    func toggle(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let key = theme.text(p["bind"]) ?? node.id
        let store = self.store
        let binding = Binding<Bool>(get: { store.bool(key) }, set: { store.setState(key, $0) })
        return AnyView(Toggle(theme.text(p["text"]) ?? "", isOn: binding))
    }

    func textField(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let key = theme.text(p["bind"]) ?? node.id
        let store = self.store
        let binding = Binding<String>(get: { store.string(key) }, set: { store.setState(key, $0) })
        let placeholder = theme.text(p["placeholder"]) ?? ""
        var view: AnyView
        if LiveJSON.bool(theme.text(p["secure"])) == true {
            view = AnyView(SecureField(placeholder, text: binding))
        } else {
            view = AnyView(TextField(placeholder, text: binding))
        }
        if theme.text(p["fieldStyle"]) != "plain" {
            view = AnyView(view.textFieldStyle(.roundedBorder))
        }
        switch theme.text(p["keyboard"]) {
        case "email":
            view = AnyView(view.keyboardType(.emailAddress).textInputAutocapitalization(.never))
        case "number":
            view = AnyView(view.keyboardType(.numberPad))
        case "decimal":
            view = AnyView(view.keyboardType(.decimalPad))
        case "phone":
            view = AnyView(view.keyboardType(.phonePad))
        case "url":
            view = AnyView(view.keyboardType(.URL).textInputAutocapitalization(.never))
        default:
            break
        }
        return view
    }

    func picker(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let key = theme.text(p["bind"]) ?? node.id
        let rawOptions = (p["options"] as? [Any]) ?? []
        let options: [String] = rawOptions.compactMap { theme.text($0) }
        let fallback = options.first ?? ""
        let store = self.store
        let binding = Binding<String>(
            get: {
                let value = store.string(key)
                return options.contains(value) ? value : fallback
            },
            set: { store.setState(key, $0) }
        )
        let base = Picker(theme.text(p["text"]) ?? "", selection: binding) {
            ForEach(options, id: \.self) { option in
                Text(option).tag(option)
            }
        }
        switch theme.text(p["pickerStyle"]) {
        case "menu": return AnyView(base.pickerStyle(.menu))
        case "wheel": return AnyView(base.pickerStyle(.wheel))
        case "inline": return AnyView(base.pickerStyle(.inline))
        default: return AnyView(base.pickerStyle(.segmented))
        }
    }

    func shape(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let fill: AnyShapeStyle = theme.fill(p["fill"]) ?? AnyShapeStyle(Color.accentColor)
        switch theme.text(p["shape"]) ?? "rect" {
        case "circle":
            return AnyView(Circle().fill(fill))
        case "capsule":
            return AnyView(Capsule().fill(fill))
        case "rounded":
            let r = theme.radius(p["radius"]) ?? 12
            return AnyView(RoundedRectangle(cornerRadius: r, style: .continuous).fill(fill))
        default:
            return AnyView(Rectangle().fill(fill))
        }
    }

    func progress(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        if let value = LiveJSON.double(theme.text(p["value"])), value.isFinite {
            let clamped = min(max(value, 0), 1)
            return AnyView(ProgressView(value: clamped))
        }
        return AnyView(ProgressView())
    }

    func custom(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let name = theme.text(p["name"]) ?? ""
        if let make = LiveComponents.registry[name] {
            let kids = expand(node.children, in: scope).map { AnyView(self.child($0)) }
            return make(LiveProps(raw: p, theme: theme, store: store, scope: scope, children: kids))
        }
        return LiveNodeView.placeholder("custom “\(name)” is not registered in Sources/Components")
    }

    static func placeholder(_ message: String) -> AnyView {
        AnyView(
            Text(message)
                .font(.caption.monospaced())
                .foregroundStyle(.red)
                .padding(6)
                .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
        )
    }

    // MARK: buttons

    func buttonVariant(_ p: JSONObject, _ theme: LiveTheme) -> String? {
        if let v = theme.text(p["variant"]) { return v }
        // Tolerate "style": "primary" when no style bundle of that name exists.
        if let s = theme.text(p["style"]), LiveNodeView.buttonVariants.contains(s) { return s }
        return nil
    }

    func buttonLabel(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        if !node.children.isEmpty {
            let items = expand(node.children, in: scope)
            let spacing = theme.spacing(p["spacing"])
            return AnyView(HStack(spacing: spacing) {
                ForEach(items) { item in self.child(item) }
            })
        }
        let title = theme.text(p["text"])
        let symbol = theme.text(p["symbol"])
        if let t = title, let s = symbol { return AnyView(Label(t, systemImage: s)) }
        if let s = symbol { return AnyView(Image(systemName: s)) }
        return AnyView(Text(title ?? "Button"))
    }

    /// Look modifiers go on the label (so padding/maxWidth grow the tappable
    /// button). With a system variant, background becomes the tint and radius
    /// becomes the button's border shape.
    func renderButton(_ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        let variant = buttonVariant(p, theme)
        let systemStyled = LiveNodeView.systemButtonVariants.contains(variant ?? "")
        var labelProps = p
        if systemStyled {
            labelProps.removeValue(forKey: "background")
            labelProps.removeValue(forKey: "radius")
            labelProps.removeValue(forKey: "glass")
        }
        var label = buttonLabel(p, theme)
        label = textModifiers(label, labelProps, theme)
        label = layoutModifiers(label, labelProps, theme)
        label = decorationModifiers(label, labelProps, theme)

        let store = self.store
        let scope = self.scope
        let action = p["action"]
        let role: ButtonRole? = variant == "destructive" ? .destructive : nil
        let finalLabel = label
        var view = AnyView(Button(role: role) {
            store.perform(action, scope: scope)
        } label: {
            finalLabel
        })
        switch variant {
        case "primary": view = AnyView(view.buttonStyle(.borderedProminent))
        case "secondary": view = AnyView(view.buttonStyle(.bordered))
        case "plain": view = AnyView(view.buttonStyle(.plain))
        case "borderless": view = AnyView(view.buttonStyle(.borderless))
        case "glass": view = AnyView(view.buttonStyle(.glass))
        case "glassProminent": view = AnyView(view.buttonStyle(.glassProminent))
        default: break
        }
        let tintColor = theme.color(p["tint"]) ?? (systemStyled ? theme.color(p["background"]) : nil)
        if let tint = tintColor {
            view = AnyView(view.tint(tint))
        }
        if systemStyled, let r = theme.radius(p["radius"]) {
            let shape: ButtonBorderShape = r >= 500 ? .capsule : .roundedRectangle(radius: r)
            view = AnyView(view.buttonBorderShape(shape))
        }
        switch theme.text(p["controlSize"]) {
        case "mini": view = AnyView(view.controlSize(.mini))
        case "small": view = AnyView(view.controlSize(.small))
        case "regular": view = AnyView(view.controlSize(.regular))
        case "large": view = AnyView(view.controlSize(.large))
        case "extraLarge": view = AnyView(view.controlSize(.extraLarge))
        default: break
        }
        return behaviorModifiers(view, p, theme, isLeaf: true, allowTap: false)
    }

    // MARK: modifiers

    func textModifiers(_ view: AnyView, _ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        var v = view
        if let font = theme.font(p) {
            v = AnyView(v.font(font))
        } else {
            if let w = LiveTheme.weight(theme.text(p["weight"])) { v = AnyView(v.fontWeight(w)) }
            if let d = LiveTheme.design(theme.text(p["design"])) { v = AnyView(v.fontDesign(d)) }
        }
        if let fg = theme.fill(p["foreground"]) {
            v = AnyView(v.foregroundStyle(fg))
        }
        if node.type != "button", let tint = theme.color(p["tint"]) {
            v = AnyView(v.tint(tint))
        }
        switch theme.text(p["textAlign"]) {
        case "center": v = AnyView(v.multilineTextAlignment(.center))
        case "trailing": v = AnyView(v.multilineTextAlignment(.trailing))
        case "leading": v = AnyView(v.multilineTextAlignment(.leading))
        default: break
        }
        if let lines = LiveJSON.int(theme.text(p["lines"])), lines >= 1 {
            v = AnyView(v.lineLimit(lines))
        }
        if LiveJSON.bool(theme.text(p["italic"])) == true {
            v = AnyView(v.italic())
        }
        return v
    }

    func layoutModifiers(_ view: AnyView, _ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        var v = view
        if node.type != "scroll", let insets = theme.insets(p["padding"]) {
            v = AnyView(v.padding(insets))
        }
        var width = theme.frameNumber(p["width"]).map { max($0, 0) }
        var height = theme.frameNumber(p["height"]).map { max($0, 0) }
        let minWidth = theme.number(p["minWidth"]).map { max($0, 0) }
        var maxWidth = theme.frameNumber(p["maxWidth"]).map { max($0, 0) }
        let minHeight = theme.number(p["minHeight"]).map { max($0, 0) }
        var maxHeight = theme.frameNumber(p["maxHeight"]).map { max($0, 0) }
        // A fixed size of "infinity" means "fill".
        if let w = width, !w.isFinite {
            maxWidth = .infinity
            width = nil
        }
        if let h = height, !h.isFinite {
            maxHeight = .infinity
            height = nil
        }
        let align = LiveNodeView.alignment(theme.text(p["frameAlign"])) ?? .center
        if width != nil || height != nil {
            v = AnyView(v.frame(width: width, height: height, alignment: align))
        }
        let isRaster = node.type == "image" && p["symbol"] == nil
        let sized = width != nil || height != nil || maxWidth != nil || maxHeight != nil
        if isRaster && sized {
            // A filled image reports a size larger than its frame; min 0 stops it
            // from widening the whole layout (the overflow is clipped below).
            v = AnyView(v.frame(minWidth: minWidth ?? 0, maxWidth: maxWidth,
                                minHeight: minHeight ?? 0, maxHeight: maxHeight, alignment: align))
        } else if minWidth != nil || maxWidth != nil || minHeight != nil || maxHeight != nil {
            v = AnyView(v.frame(minWidth: minWidth, maxWidth: maxWidth,
                                minHeight: minHeight, maxHeight: maxHeight, alignment: align))
        }
        if isRaster {
            v = AnyView(v.clipped())
        }
        if let offset = p["offset"] as? JSONObject {
            v = AnyView(v.offset(x: theme.number(offset["x"]) ?? 0, y: theme.number(offset["y"]) ?? 0))
        }
        return v
    }

    func decorationModifiers(_ view: AnyView, _ p: JSONObject, _ theme: LiveTheme) -> AnyView {
        var v = view
        let radius = theme.radius(p["radius"])
        if let bg = theme.fill(p["background"]) {
            v = AnyView(v.background(bg))
        }
        if let r = radius, node.type != "shape" {
            v = AnyView(v.clipShape(RoundedRectangle(cornerRadius: r, style: .continuous)))
        }
        if let border = p["border"] as? JSONObject {
            let color = theme.color(border["color"]) ?? Color.gray.opacity(0.3)
            let width = theme.number(border["width"]) ?? 1
            let r = radius ?? 0
            v = AnyView(v.overlay {
                RoundedRectangle(cornerRadius: r, style: .continuous).strokeBorder(color, lineWidth: width)
            })
        }
        v = glassModifier(v, p, theme, radius: radius)
        if let s = theme.shadow(p["shadow"]) {
            v = AnyView(v.shadow(color: s.color, radius: s.radius, x: s.x, y: s.y))
        }
        if let o = LiveJSON.double(theme.text(p["opacity"])), o.isFinite {
            v = AnyView(v.opacity(min(max(o, 0), 1)))
        }
        if LiveJSON.bool(theme.text(p["ignoreSafeArea"])) == true {
            v = AnyView(v.ignoresSafeArea())
        }
        return v
    }

    /// Liquid Glass (iOS 26): "glass": true | "regular" | "clear".
    func glassModifier(_ view: AnyView, _ p: JSONObject, _ theme: LiveTheme, radius: CGFloat?) -> AnyView {
        guard let raw = p["glass"] else { return view }
        let kind = theme.text(raw) ?? "regular"
        if kind == "false" || kind == "none" { return view }
        let shape = RoundedRectangle(cornerRadius: radius ?? 20, style: .continuous)
        if kind == "clear" {
            return AnyView(view.glassEffect(.clear, in: shape))
        }
        return AnyView(view.glassEffect(.regular, in: shape))
    }

    /// Pick mode (select an element for ./ui), tap actions, disabled, and the
    /// brief outline flashed on elements changed by the last ./ui apply.
    ///
    /// The modifier chain is identical whether or not pick mode / flash is on
    /// (only parameters change). If the wrapped type changed, SwiftUI would
    /// rebuild the subtree and e.g. reset scroll position on every edit.
    func behaviorModifiers(_ view: AnyView, _ p: JSONObject, _ theme: LiveTheme,
                           isLeaf: Bool, allowTap: Bool) -> AnyView {
        var v = view
        if LiveJSON.bool(theme.text(p["disabled"])) == true {
            v = AnyView(v.disabled(true))
        }
        let store = self.store
        let scope = self.scope
        let id = node.id
        let type = node.type
        let instance = key
        let row = scope.index
        let picking = store.picking
        let action: Any? = allowTap ? p["onTap"] : nil
        if isLeaf {
            if let tapAction = action {
                v = AnyView(v.contentShape(Rectangle()).onTapGesture {
                    store.perform(tapAction, scope: scope)
                })
            }
            v = AnyView(v.allowsHitTesting(!picking).overlay {
                Color.clear
                    .contentShape(Rectangle())
                    .onTapGesture { store.select(id: id, type: type, instance: instance, row: row) }
                    .allowsHitTesting(picking)
            })
        } else {
            if action != nil {
                v = AnyView(v.contentShape(Rectangle()))
            }
            let mask: GestureMask = (picking || action != nil) ? .all : .subviews
            let tap = TapGesture().onEnded {
                if store.picking {
                    store.select(id: id, type: type, instance: instance, row: row)
                } else if let tapAction = action {
                    store.perform(tapAction, scope: scope)
                }
            }
            v = AnyView(v.gesture(tap, including: mask))
        }
        let flashing = store.flashIds.contains(id)
        let r = min(max(theme.radius(p["radius"]) ?? 6, 6), 28)
        v = AnyView(v.overlay { LiveFlash(active: flashing, radius: r) })
        return v
    }
}

/// Orange outline shown briefly on elements the last edit changed.
struct LiveFlash: View {
    let active: Bool
    let radius: CGFloat

    var body: some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .stroke(Color.orange, lineWidth: 2)
            .opacity(active ? 1 : 0)
            .allowsHitTesting(false)
    }
}
