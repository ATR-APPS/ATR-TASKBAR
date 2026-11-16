// Now-playing widget at the bottom-left of the screen, next to the Dock.
// Square cover + title / artist; left click toggles play / pause.

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

final class MediaWidget {
    var onClick: (() -> Void)? {
        didSet { hit.onClick = onClick }
    }
    var enabled = true {
        didSet { update() }
    }

    private let hit = HitView(frame: .zero)
    private let panel: DockPanel
    private let row: TileRow

    private var media: Media?
    private var shown: (media: Media, band: DockBand, art: Bool)?
    private var artwork: [URL: NSImage] = [:]
    private var loading: Set<URL> = []

    init() {
        panel = DockPanel(content: hit)
        row = TileRow(in: hit)
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
