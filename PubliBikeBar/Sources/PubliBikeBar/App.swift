import AppKit
import Observation
import SwiftUI

/// Status item + NSPopover instead of SwiftUI's MenuBarExtra: the popover stays anchored
/// under the icon and resizes correctly when its content changes height.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private let store = Store()
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePopover)

        popover.behavior = .transient
        popover.animates = false
        popover.delegate = self

        updateLabel()
    }

    @objc private func togglePopover() {
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        guard let button = statusItem.button else { return }
        // Fresh view each time: resets the search field and focuses it.
        let host = NSHostingController(rootView: PanelView().environment(store))
        host.sizingOptions = [.preferredContentSize]
        popover.contentViewController = host
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        NSApp.activate(ignoringOtherApps: true)
        popover.contentViewController?.view.window?.makeKey()
    }

    func popoverDidClose(_ notification: Notification) {
        popover.contentViewController = nil
    }

    /// Re-render the status item whenever the observed store values change.
    private func updateLabel() {
        let image = withObservationTracking {
            MenuBarLabel.image(for: store)
        } onChange: { [weak self] in
            Task { @MainActor in self?.updateLabel() }
        }
        statusItem.button?.image = image
    }
}

enum MenuBarLabel {
    /// Counts for the nearest (or pinned) favourite, or a plain bicycle icon.
    @MainActor
    static func image(for store: Store) -> NSImage {
        if let id = store.menuBarStation, let s = store.live[id] {
            return render(e: s.eBikes, m: s.bikes, warn: store.error != nil)
        }
        let img = NSImage(systemSymbolName: "bicycle", accessibilityDescription: "PubliBike")?
            .withSymbolConfiguration(.init(pointSize: 14, weight: .regular)) ?? NSImage()
        img.isTemplate = true
        return img
    }

    /// Vector-drawn (drawingHandler) so it's crisp and unclipped on both Retina and 1x displays.
    static func render(e: Int, m: Int, warn: Bool) -> NSImage {
        let font = NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .medium)
        let text = NSMutableAttributedString()

        func symbol(_ name: String, size: CGFloat) {
            let config = NSImage.SymbolConfiguration(pointSize: size, weight: .semibold)
            guard let img = NSImage(systemSymbolName: name, accessibilityDescription: nil)?
                .withSymbolConfiguration(config) else { return }
            let attachment = NSTextAttachment()
            attachment.image = img
            // Centre the symbol on the digits' cap height.
            attachment.bounds = CGRect(x: 0, y: (font.capHeight - img.size.height) / 2,
                                       width: img.size.width, height: img.size.height)
            text.append(NSAttributedString(attachment: attachment))
        }
        func string(_ s: String) {
            text.append(NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: NSColor.black]))
        }

        symbol("bolt.fill", size: 11)
        string(" \(e)  ")
        symbol("bicycle", size: 12)
        string(" \(m)")
        if warn {
            string(" ")
            symbol("exclamationmark.triangle.fill", size: 10)
        }

        let bounds = text.boundingRect(with: .zero, options: [.usesLineFragmentOrigin, .usesFontLeading])
        let size = NSSize(width: ceil(bounds.width) + 2, height: max(18, ceil(bounds.height) + 2))
        let image = NSImage(size: size, flipped: false) { rect in
            text.draw(at: NSPoint(x: 1, y: (rect.height - bounds.height) / 2 - bounds.origin.y))
            return true
        }
        image.isTemplate = true
        return image
    }
}
