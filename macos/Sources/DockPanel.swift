// Shared pieces of the widgets that sit next to the Dock: where the Dock
// band is, a floating glass panel on the Dock's level, and the
// "square tile + title / subtitle" row they all draw.

import AppKit

/// The strip the Dock occupies at the bottom of its screen.
struct DockBand: Equatable {
    let y: CGFloat
    let height: CGFloat
    let minX: CGFloat  // free space for widgets, already inset
    let maxX: CGFloat

    /// Screen that holds the Dock, widgets centered on the Dock's band.
    /// Falls back to a fixed height when the Dock is hidden or on a side.
    static func current() -> DockBand? {
        let screens = NSScreen.screens
        guard let main = screens.first else { return nil }
        let screen = screens.first {
            $0.visibleFrame.minY > $0.frame.minY || $0.visibleFrame.minX > $0.frame.minX
        } ?? main
        let full = screen.frame
        let visible = screen.visibleFrame
        let dock = visible.minY - full.minY  // 0 when the Dock is on a side or hidden
        // the band is the Dock bar plus its margins: match the bar itself
        let height: CGFloat = dock > 24 ? max(dock - 12, 28).rounded() : 44
        let y = dock > 24 ? full.minY + (dock - height) / 2 : full.minY + 6
        return DockBand(y: y.rounded(), height: height, minX: visible.minX + 8, maxX: visible.maxX - 8)
    }
}

/// Borderless panel on the Dock's window level, on all Spaces but not over
/// fullscreen apps. Liquid Glass on macOS 26, blurred material before.
final class DockPanel {
    let content: NSView
    private let panel: NSPanel
    private(set) var visible = false

    init(content: NSView) {
        self.content = content
        panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: true)
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.dockWindow)))
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.alphaValue = 0

        if #available(macOS 26.0, *) {
            let glass = NSGlassEffectView()
            glass.contentView = content
            panel.contentView = glass
            panel.hasShadow = false
        } else {
            let blur = NSVisualEffectView()
            blur.material = .popover
            blur.blendingMode = .behindWindow
            blur.state = .active
            blur.wantsLayer = true
            blur.layer?.masksToBounds = true
            blur.addSubview(content)
            content.autoresizingMask = [.width, .height]
            panel.contentView = blur
            panel.hasShadow = true
        }
    }

    func place(_ frame: NSRect) {
        if panel.frame != frame {
            panel.setFrame(frame, display: true)
        }
        content.frame = NSRect(origin: .zero, size: frame.size)
        let radius = frame.height * 0.32
        if #available(macOS 26.0, *), let glass = panel.contentView as? NSGlassEffectView {
            glass.cornerRadius = radius
        } else {
            panel.contentView?.layer?.cornerRadius = radius
        }
    }

    func show() {
        guard !visible else { return }
        visible = true
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 1
        }
    }

    func hide() {
        guard visible else { return }
        visible = false
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self, !self.visible else { return }
            self.panel.orderOut(nil)
        })
    }
}

/// Square tile + title / subtitle; everything scales with the Dock height
/// (13 / 11 pt at the default size).
final class TileRow {
    let tile = NSImageView()
    let title = NSTextField(labelWithString: "")
    let subtitle = NSTextField(labelWithString: "")

    init(in view: NSView) {
        tile.wantsLayer = true
        tile.layer?.masksToBounds = true
        tile.layer?.backgroundColor = NSColor.systemGray.withAlphaComponent(0.35).cgColor
        title.textColor = .labelColor
        subtitle.textColor = .secondaryLabelColor
        for label in [title, subtitle] {
            label.lineBreakMode = .byTruncatingTail
            label.maximumNumberOfLines = 1
        }
        [tile, title, subtitle].forEach(view.addSubview)
    }

    /// A square picture, or an SF Symbol drawn centered on the gray tile.
    func setTile(_ image: NSImage?, symbol: String, height h: CGFloat) {
        if let image {
            tile.image = image
            tile.imageScaling = .scaleAxesIndependently
        } else {
            tile.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: h * 0.3, weight: .regular))
            tile.imageScaling = .scaleNone
        }
    }

    /// Lays the row out from x = 0 and returns its width.
    func layout(height h: CGFloat, maxText: CGFloat) -> CGFloat {
        let scale = h / 47
        let tileSize = (h * 0.72).rounded()
        let pad = ((h - tileSize) / 2).rounded()
        let gap = (10 * scale).rounded()
        title.font = .systemFont(ofSize: (13 * scale).rounded(), weight: .semibold)
        subtitle.font = .systemFont(ofSize: (11 * scale).rounded())

        title.sizeToFit()
        subtitle.sizeToFit()
        let hasSub = !subtitle.stringValue.isEmpty
        let textW = min((maxText * scale).rounded(), max(title.frame.width, hasSub ? subtitle.frame.width : 0))
        let th = title.frame.height
        let sh = hasSub ? subtitle.frame.height : 0
        let x = pad + tileSize + gap
        let y0 = ((h - th - sh) / 2).rounded()

        tile.frame = NSRect(x: pad, y: pad, width: tileSize, height: tileSize)
        tile.layer?.cornerRadius = max(4, tileSize * 0.14)
        title.frame = NSRect(x: x, y: y0 + sh, width: textW, height: th)
        subtitle.frame = NSRect(x: x, y: y0, width: textW, height: sh)
        subtitle.isHidden = !hasSub
        return x + textW + pad + 4
    }
}

func squared(_ img: NSImage) -> NSImage {
    guard let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return img }
    let s = min(cg.width, cg.height)
    let rect = CGRect(x: (cg.width - s) / 2, y: (cg.height - s) / 2, width: s, height: s)
    guard let crop = cg.cropping(to: rect) else { return img }
    return NSImage(cgImage: crop, size: NSSize(width: s, height: s))
}

/// Menu item that runs a closure.
final class ActionItem: NSMenuItem {
    private let handler: () -> Void

    init(_ title: String, image: NSImage? = nil, handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        if let image {
            image.size = NSSize(width: 16, height: 16)
            self.image = image
        }
    }

    required init(coder: NSCoder) { fatalError() }

    @objc private func fire() { handler() }
}
