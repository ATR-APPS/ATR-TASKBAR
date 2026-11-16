// Now-playing widget at the bottom-left of the screen, next to the Dock.
// Square cover + title / artist; a click unfolds previous / play-pause / next
// controls above it.

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

private final class ControlButton: NSButton {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

final class MediaWidget {
    var onCommand: ((MediaCommand) -> Void)?
    var enabled = true {
        didSet { update() }
    }

    private let hit = HitView(frame: .zero)
    private let panel: DockPanel
    private let row: TileRow

    private let controlsView = NSView()
    private let controls: DockPanel
    private var buttons: [(button: NSButton, command: MediaCommand)] = []
    private var expanded = false
    private var outsideClicks: Any?

    private var media: Media?
    private var shown: (media: Media, band: DockBand, art: Bool)?
    private var artwork: [URL: NSImage] = [:]
    private var loading: Set<URL> = []

    init() {
        panel = DockPanel(content: hit)
        row = TileRow(in: hit)
        controls = DockPanel(content: controlsView)
        for command in [MediaCommand.previous, .toggle, .next] {
            let b = ControlButton(frame: .zero)
            b.isBordered = false
            b.imagePosition = .imageOnly
            b.contentTintColor = .labelColor
            b.target = self
            b.action = #selector(pressed(_:))
            controlsView.addSubview(b)
            buttons.append((b, command))
        }
        hit.onClick = { [weak self] in self?.setExpanded(!(self?.expanded ?? true)) }
    }

    // MARK: - Public

    func show(_ media: Media?) {
        self.media = media
        update()
    }

    /// Re-checks media, Dock geometry and settings; cheap when nothing changed.
    func update() {
        guard enabled, let m = media, let band = DockBand.current() else {
            shown = nil
            setExpanded(false)
            panel.hide()
            return
        }
        let art = image(for: m)
        if let s = shown, s.media == m, s.band == band, s.art == (art != nil) {
            return
        }

        row.title.stringValue = m.title
        row.subtitle.stringValue = m.artist
        row.setTile(art, symbol: "music.note", height: band.height)
        let width = row.layout(height: band.height, maxText: 220)
        panel.place(NSRect(x: band.minX, y: band.y, width: width, height: band.height))
        hit.alphaValue = m.playing ? 1 : 0.55
        shown = (m, band, art != nil)
        panel.show()
        layoutControls()
    }

    // MARK: - Controls

    @objc private func pressed(_ sender: NSButton) {
        guard let command = buttons.first(where: { $0.button === sender })?.command else { return }
        onCommand?(command)
    }

    private func setExpanded(_ on: Bool) {
        guard on != expanded else { return }
        expanded = on
        if on {
            layoutControls()
            controls.show(rise: 10)
            // a click anywhere else folds the controls back
            outsideClicks = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) {
                [weak self] _ in self?.setExpanded(false)
            }
        } else {
            controls.hide()
            if let m = outsideClicks { NSEvent.removeMonitor(m) }
            outsideClicks = nil
        }
    }

    /// Same width as the widget, stacked right above it.
    private func layoutControls() {
        guard let (m, band, _) = shown else { return }
        let h = (band.height * 0.8).rounded()
        let width = row.layout(height: band.height, maxText: 220)
        let symbol = NSImage.SymbolConfiguration(pointSize: (h * 0.36).rounded(), weight: .semibold)
        let slot = width / CGFloat(buttons.count)
        for (i, item) in buttons.enumerated() {
            let name: String
            switch item.command {
            case .previous: name = "backward.fill"
            case .toggle: name = m.playing ? "pause.fill" : "play.fill"
            case .next: name = "forward.fill"
            }
            item.button.image = NSImage(systemSymbolName: name, accessibilityDescription: nil)?
                .withSymbolConfiguration(symbol)
            item.button.frame = NSRect(x: (slot * CGFloat(i)).rounded(), y: 0, width: slot.rounded(), height: h)
        }
        let gap = (band.height * 0.15).rounded()
        controls.place(NSRect(x: band.minX, y: band.y + band.height + gap, width: width, height: h))
    }

    // MARK: - Internals

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
