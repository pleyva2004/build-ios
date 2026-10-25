import SwiftUI

// The UI lives in design/*.json and is rendered by Sources/Runtime. While the
// app runs in the Simulator it hot-reloads those files from your Mac.
@main
struct __APP_NAME__App: App {
    @StateObject private var store = LiveStore()

    var body: some Scene {
        WindowGroup {
            LiveRootView()
                .environmentObject(store)
        }
    }
}
