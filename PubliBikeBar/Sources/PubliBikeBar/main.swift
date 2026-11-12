import AppKit

// Plain AppKit entry point. A SwiftUI `App` needs at least one window scene, and
// activating the app (to focus the popover's search field) would open it.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
