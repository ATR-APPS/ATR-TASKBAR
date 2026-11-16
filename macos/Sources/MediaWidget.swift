// Floating now-playing widget at the bottom-left of the screen, on the same
// level and band as the Dock. Square cover + title / artist; left click
// toggles play / pause. Lives on all Spaces but not over fullscreen apps.

import AppKit

private final class HitView: NSView {
    var onClick: (() -> Void)?

    override init(frame: NSRect) {
        super.init(frame: frame)
        addTrackingArea(NSTrackingArea(rect: .zero,
                                       options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self))
    }

    required init?(coder: NSCoder) { fatalError() }

    // swallow clicks on the cover / labels too
    override func hitTest(_ point: NSPoint) -> NSView? { frame.contains(point) ? self : nil }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {}
    override func mouseUp(with event: NSEvent) {
        if bounds.contains(convert(event.locationInWindow, from: nil)) { onClick?() }
    }
    override func mouseEntered(with event: NSEvent) { NSCursor.pointingHand.push() }
    override func mouseExited(with event: NSEvent) { NSCursor.pop() }
}

private func squared(_ img: NSImage) -> NSImage {
    guard let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return img }
    let s = min(cg.width, cg.height)
    let rect = CGRect(x: (cg.width - s) / 2, y: (cg.height - s) / 2, width: s, height: s)
    guard let crop = cg.cropping(to: rect) else { return img }
    return NSImage(cgImage: crop, size: NSSize(width: s, height: s))
}

final class MediaWidget {
    var onClick: (() -> Void)? {
        didSet { hit.onClick = onClick }
    }
    var enabled = true {
        didSet { update() }
    }

    private let panel: NSPanel
    private let hit = HitView(frame: .zero)
    private let cover = NSImageView()
    private let titleLabel = NSTextField(labelWithString: "")
    private let artistLabel = NSTextField(labelWithString: "")

    private var media: Media?
    private var shown: (media: Media, frame: NSRect, art: Bool)?
    private var visible = false
    private var artwork: [URL: NSImage] = [:]
    private var loading: Set<URL> = []

    init() {
        panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: true)
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.dockWindow)))
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.alphaValue = 0

        // Dock look: Liquid Glass on macOS 26, blurred material before
        let background: NSView
        if #available(macOS 26.0, *) {
            let glass = NSGlassEffectView()
            glass.contentView = hit
            background = glass
            panel.hasShadow = false
        } else {
            let blur = NSVisualEffectView()
            blur.material = .popover
            blur.blendingMode = .behindWindow
            blur.state = .active
            blur.wantsLayer = true
            blur.layer?.masksToBounds = true
            blur.addSubview(hit)
            hit.autoresizingMask = [.width, .height]
            background = blur
            panel.hasShadow = true
        }
        panel.contentView = background

        cover.imageScaling = .scaleAxesIndependently
        cover.wantsLayer = true
        cover.layer?.masksToBounds = true
        cover.layer?.backgroundColor = NSColor.systemGray.withAlphaComponent(0.35).cgColor

        titleLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        titleLabel.textColor = .labelColor
        artistLabel.font = .systemFont(ofSize: 11)
        artistLabel.textColor = .secondaryLabelColor
        for label in [titleLabel, artistLabel] {
            label.lineBreakMode = .byTruncatingTail
            label.maximumNumberOfLines = 1
        }
        [cover, titleLabel, artistLabel].forEach(hit.addSubview)
    }

    // MARK: - Public

    func show(_ media: Media?) {
        self.media = media
        update()
    }

    /// Re-checks media, Dock geometry and settings; cheap when nothing changed.
    func update() {
        guard enabled, let m = media, let slot = Self.slot() else {
            hide()
            return
        }
        let art = image(for: m)
        let frame = NSRect(origin: slot.origin, size: NSSize(width: 0, height: slot.height))
        if let s = shown, s.media == m, s.frame.origin == frame.origin,
           s.frame.height == frame.height, s.art == (art != nil) {
            return
        }

        titleLabel.stringValue = m.title
        artistLabel.stringValue = m.artist
        if let art {
            cover.image = art
            cover.imageScaling = .scaleAxesIndependently
        } else {
            cover.image = NSImage(systemSymbolName: "music.note", accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: slot.height * 0.3, weight: .regular))
            cover.imageScaling = .scaleNone
        }
        let width = layout(height: slot.height)
        panel.setFrame(NSRect(x: frame.minX, y: frame.minY, width: width, height: slot.height), display: true)
        hit.alphaValue = m.playing ? 1 : 0.55
        shown = (m, frame, art != nil)

        if !visible {
            visible = true
            panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.2
                panel.animator().alphaValue = 1
            }
        }
    }

    // MARK: - Internals

    private func hide() {
        shown = nil
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

    /// Bottom-left corner of the screen that holds the Dock, centered on the
    /// Dock's band. Falls back to a fixed height when the Dock is hidden.
    private static func slot() -> (origin: NSPoint, height: CGFloat)? {
        let screens = NSScreen.screens
        guard let main = screens.first else { return nil }
        let screen = screens.first {
            $0.visibleFrame.minY > $0.frame.minY || $0.visibleFrame.minX > $0.frame.minX
        } ?? main
        let full = screen.frame
        let dock = screen.visibleFrame.minY - full.minY  // 0 when the Dock is on a side or hidden
        let height: CGFloat = dock > 24 ? min(max(dock - 12, 36), 64) : 44
        let y = dock > 24 ? full.minY + (dock - height) / 2 : full.minY + 6
        return (NSPoint(x: screen.visibleFrame.minX + 8, y: y.rounded()), height)
    }

    private func layout(height h: CGFloat) -> CGFloat {
        let coverSize = (h * 0.72).rounded()
        let pad = ((h - coverSize) / 2).rounded()
        let gap: CGFloat = 10
        let maxText: CGFloat = 220

        titleLabel.sizeToFit()
        artistLabel.sizeToFit()
        let hasArtist = !artistLabel.stringValue.isEmpty
        let textW = min(maxText, max(titleLabel.frame.width, hasArtist ? artistLabel.frame.width : 0))
        let th = titleLabel.frame.height
        let ah = hasArtist ? artistLabel.frame.height : 0
        let x = pad + coverSize + gap
        let y0 = ((h - th - ah) / 2).rounded()

        cover.frame = NSRect(x: pad, y: pad, width: coverSize, height: coverSize)
        cover.layer?.cornerRadius = max(4, coverSize * 0.14)
        titleLabel.frame = NSRect(x: x, y: y0 + ah, width: textW, height: th)
        artistLabel.frame = NSRect(x: x, y: y0, width: textW, height: ah)
        artistLabel.isHidden = !hasArtist

        let width = x + textW + pad + 4
        hit.frame = NSRect(x: 0, y: 0, width: width, height: h)
        if #available(macOS 26.0, *), let glass = panel.contentView as? NSGlassEffectView {
            glass.cornerRadius = h * 0.32
        } else {
            panel.contentView?.layer?.cornerRadius = h * 0.32
        }
        return width
    }

    private func image(for m: Media) -> NSImage? {
        guard let url = m.artwork else { return nil }
        if let img = artwork[url] { return img }
        if loading.insert(url).inserted {
            URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
                let img = data.flatMap(NSImage.init(data:)).map(squared)
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.loading.remove(url)
                    guard let img else { return }
                    if self.artwork.count > 30 { self.artwork.removeAll() }
                    self.artwork[url] = img
                    if self.media?.artwork == url { self.update() }
                }
            }.resume()
        }
        return nil
    }
}
