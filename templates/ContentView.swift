import SwiftUI

struct ContentView: View {
    @State private var count = 0

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "swift")
                .font(.system(size: 72))
                .foregroundStyle(.orange)

            Text("Hello, iOS")
                .font(.largeTitle.bold())

            Text("Taps: \(count)")
                .font(.title2)
                .monospacedDigit()
                .contentTransition(.numericText())

            Button("Tap me") {
                withAnimation { count += 1 }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
        .padding()
    }
}

// Tip: for a live canvas preview in Xcode, add:
//     #Preview { ContentView() }
// It is intentionally omitted so headless `xcodebuild` runs never depend on the
// Swift macro plugin (swift-plugin-server), which can fail on a freshly installed
// Xcode until macOS finishes verifying the app bundle.
