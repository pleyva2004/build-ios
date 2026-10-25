// LiveRoot.swift — app shell for the live UI: tabs or a single stack, one
// NavigationStack per tab, sheets, alerts, and the dev banners (design errors,
// pick mode). Part of the live-UI runtime.
import SwiftUI
import UIKit

struct LiveRootView: View {
    @EnvironmentObject var store: LiveStore
    @Environment(\.colorScheme) var colorScheme

    var body: some View {
        rootContent
            .tint(tintColor)
            .preferredColorScheme(preferredScheme)
            .animation(.easeInOut(duration: 0.25), value: store.specVersion)
            .sheet(item: $store.sheet) { route in
                LiveSheetView(route: route)
                    .environmentObject(store)
            }
            .alert(store.alertTitle, isPresented: store.alertBinding(inSheet: false)) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(store.alertMessage)
            }
            .overlay(alignment: .top) {
                LiveBanners()
                    .allowsHitTesting(false)
            }
    }

    @ViewBuilder var rootContent: some View {
        if store.spec.tabs.isEmpty {
            LiveStackView(tabId: "_root", rootScreen: store.spec.rootScreen)
        } else {
            TabView(selection: $store.selectedTab) {
                ForEach(store.spec.tabs) { tab in
                    LiveStackView(tabId: tab.id, rootScreen: tab.screen)
                        .tabItem { Label(tab.title, systemImage: tab.symbol) }
                        .tag(tab.id)
                }
            }
        }
    }

    var tintColor: Color? {
        let theme = LiveTheme(tokens: store.spec.tokens, dark: colorScheme == .dark, scope: LiveScope())
        return theme.color(store.spec.tint)
    }

    var preferredScheme: ColorScheme? {
        switch store.spec.colorScheme {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }
}

/// One navigation stack (a tab, or the whole app when there are no tabs).
struct LiveStackView: View {
    let tabId: String
    let rootScreen: String
    @EnvironmentObject var store: LiveStore

    var body: some View {
        NavigationStack(path: store.pathBinding(tabId)) {
            LiveScreenView(screenId: rootScreen, params: [:])
                .navigationDestination(for: LiveRoute.self) { route in
                    LiveScreenView(screenId: route.screen, params: route.params)
                }
        }
    }
}

struct LiveSheetView: View {
    let route: LiveRoute
    @EnvironmentObject var store: LiveStore

    var body: some View {
        NavigationStack(path: store.sheetPathBinding) {
            LiveScreenView(screenId: route.screen, params: route.params)
                .navigationDestination(for: LiveRoute.self) { next in
                    LiveScreenView(screenId: next.screen, params: next.params)
                }
        }
        // A presented sheet blocks alerts on the root view, so sheets have their own.
        .alert(store.alertTitle, isPresented: store.alertBinding(inSheet: true)) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.alertMessage)
        }
        .overlay(alignment: .top) {
            LiveBanners()
                .allowsHitTesting(false)
        }
    }
}

/// A screen: its body node plus title, title style, background and toolbar.
struct LiveScreenView: View {
    let screenId: String
    let params: [String: String]
    @EnvironmentObject var store: LiveStore
    @Environment(\.colorScheme) var colorScheme

    var body: some View {
        guard let screen = store.spec.screens[screenId] else {
            let detail = store.error ?? "Add design/screens/\(screenId).json"
            return AnyView(LiveMessage(title: "Screen “\(screenId)” not found", detail: detail))
        }
        let scope = LiveScope(item: [:], params: params, data: screen.data, state: store.state, index: nil)
        let theme = LiveTheme(tokens: store.spec.tokens, dark: colorScheme == .dark, scope: scope)
        return decorated(content(screen, scope), screen: screen, theme: theme, scope: scope)
    }

    func content(_ screen: LiveScreen, _ scope: LiveScope) -> AnyView {
        guard let root = screen.body else {
            return AnyView(LiveMessage(title: "Empty screen", detail: "Give \(screen.id) a \"body\" node."))
        }
        return AnyView(LiveNodeView(node: root, scope: scope, key: root.id))
    }

    func decorated(_ view: AnyView, screen: LiveScreen, theme: LiveTheme, scope: LiveScope) -> AnyView {
        var v = AnyView(view.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top))
        if let bg = theme.fill(screen.background) {
            v = AnyView(v.background(bg))
        }
        v = AnyView(v.navigationTitle(theme.text(screen.title) ?? ""))
        switch screen.titleDisplay {
        case "inline":
            v = AnyView(v.navigationBarTitleDisplayMode(.inline))
        case "hidden":
            v = AnyView(v.toolbar(.hidden, for: .navigationBar))
        default:
            v = AnyView(v.navigationBarTitleDisplayMode(.large))
        }
        // Toolbar items are plain native buttons (no overlays) so the system can
        // render them as standard bar buttons.
        let items = screen.toolbar.map { LiveRenderItem(id: $0.id, node: $0, scope: scope) }
        let leading = items.filter { ($0.node.props["placement"] as? String) == "leading" }
        let trailing = items.filter { ($0.node.props["placement"] as? String) != "leading" }
        if !leading.isEmpty {
            v = AnyView(v.toolbar {
                ToolbarItemGroup(placement: .topBarLeading) {
                    ForEach(leading) { item in
                        LiveToolbarButton(item: item)
                    }
                }
            })
        }
        if !trailing.isEmpty {
            v = AnyView(v.toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    ForEach(trailing) { item in
                        LiveToolbarButton(item: item)
                    }
                }
            })
        }
        return v
    }
}

struct LiveToolbarButton: View {
    let item: LiveRenderItem
    @EnvironmentObject var store: LiveStore
    @Environment(\.colorScheme) var colorScheme

    var body: some View {
        let theme = LiveTheme(tokens: store.spec.tokens, dark: colorScheme == .dark, scope: item.scope)
        let p = theme.effectiveProps(item.node.props)
        if let condition = theme.text(p["visibleIf"]), !store.isVisible(condition) {
            return AnyView(EmptyView())
        }
        let title = theme.text(p["text"])
        let symbol = theme.text(p["symbol"])
        let action = p["action"] ?? p["onTap"]
        let liveStore = self.store
        let scope = item.scope
        let label: AnyView
        if let t = title, let s = symbol {
            label = AnyView(Label(t, systemImage: s))
        } else if let s = symbol {
            label = AnyView(Image(systemName: s))
        } else {
            label = AnyView(Text(title ?? "Button"))
        }
        var view = AnyView(Button {
            liveStore.perform(action, scope: scope)
        } label: {
            label
        })
        if let tint = theme.color(p["tint"]) ?? theme.color(p["foreground"]) {
            view = AnyView(view.tint(tint))
        }
        return view
    }
}

struct LiveMessage: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundStyle(.orange)
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Dev-only banners: the current design error (the last good design stays on
/// screen) and the pick-mode hint.
struct LiveBanners: View {
    @EnvironmentObject var store: LiveStore

    var body: some View {
        VStack(spacing: 6) {
            if store.showNotLiveHint {
                Text("Not live · showing the bundled design (run ./run.sh)")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(Color.gray.opacity(0.85), in: Capsule())
            }
            if let message = store.error {
                Text("design error · \(message)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.white)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.92), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
            if store.picking {
                Text("Pick mode · tap an element")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color.pink.opacity(0.92), in: Capsule())
            }
        }
        .padding(.horizontal, 12)
    }
}
