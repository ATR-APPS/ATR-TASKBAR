// Grid that opens above the shelf tile, like the Dock's "Grid" view of a
// stack: large previews with names. Click opens a file, dragging one takes
// it out of the shelf, right click shows it in Finder or removes it, and
// files dropped on the grid join the shelf. Closes on Esc or clicking away.

import AppKit

private let cell = NSSize(width: 118, height: 124)
private let thumbSide: CGFloat = 84
private let maxColumns = 6
private let maxVisibleRows = 4
private let titleHeight: CGFloat = 40
private let footerHeight: CGFloat = 44
private let margin: CGFloat = 14

private final class KeyPanel: NSPanel {
    var onCancel: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { onCancel?() }
}

private final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// Accepts files dropped anywhere on the grid.
private final class DropView: NSView {
    var onDrop: (([URL]) -> Void)?
    weak var source: AnyObject?

    private func urls(_ info: NSDraggingInfo) -> [URL] {
        info.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                                            options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
    }

    override func draggingEntered(_ info: NSDraggingInfo) -> NSDragOperation {
        (info.draggingSource as AnyObject?) !== source && !urls(info).isEmpty ? .copy : []
    }

    override func performDragOperation(_ info: NSDraggingInfo) -> Bool {
        let dropped = urls(info)
        guard !dropped.isEmpty else { return false }
        onDrop?(dropped)
        return true
    }
}

private final class GridItem: NSView {
    let url: URL
    private weak var owner: ShelfGrid?
    let thumb = NSImageView()
    private let label = NSTextField(labelWithString: "")
    private var pressed: NSEvent?

    init(url: URL, image: NSImage, owner: ShelfGrid) {
        self.url = url
        self.owner = owner
        super.init(frame: NSRect(origin: .zero, size: cell))
        wantsLayer = true
        layer?.cornerRadius = 10

        thumb.image = image
        thumb.imageScaling = .scaleProportionallyUpOrDown
        thumb.frame = NSRect(x: (cell.width - thumbSide) / 2, y: 8, width: thumbSide, height: thumbSide)

        label.stringValue = url.lastPathComponent
        label.font = .systemFont(ofSize: 12)
        label.alignment = .center
        label.lineBreakMode = .byTruncatingMiddle
        label.maximumNumberOfLines = 1
        label.frame = NSRect(x: 4, y: thumbSide + 14, width: cell.width - 8, height: 18)
        [thumb, label].forEach(addSubview)

        toolTip = url.lastPathComponent
        addTrackingArea(NSTrackingArea(rect: .zero,
                                       options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self))
    }

    required init?(coder: NSCoder) { fatalError() }

    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { frame.contains(point) ? self : nil }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseEntered(with event: NSEvent) {
        layer?.backgroundColor = NSColor.labelColor.withAlphaComponent(0.1).cgColor
    }
    override func mouseExited(with event: NSEvent) { layer?.backgroundColor = nil }

    override func mouseDown(with event: NSEvent) { pressed = event }

    override func mouseDragged(with event: NSEvent) {
        guard let down = pressed else { return }
        let a = down.locationInWindow, b = event.locationInWindow
        guard hypot(b.x - a.x, b.y - a.y) > 3 else { return }
        pressed = nil
        owner?.beginDrag(self, event: event)
    }

    override func mouseUp(with event: NSEvent) {
        defer { pressed = nil }
        if pressed != nil { owner?.open(url) }
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        let menu = NSMenu()
        menu.addItem(ActionItem("Abrir") { [weak owner, url] in owner?.open(url) })
        menu.addItem(ActionItem("Mostrar en Finder") { [weak owner, url] in owner?.reveal(url) })
        menu.addItem(.separator())
        menu.addItem(ActionItem("Quitar de la bandeja") { [weak owner, url] in owner?.onRemove?([url]) })
        return menu
    }
}

final class ShelfGrid: NSObject, NSWindowDelegate, NSDraggingSource {
    var files: () -> [URL] = { [] }
    var thumbnail: (URL) -> NSImage = { NSWorkspace.shared.icon(forFile: $0.path) }
    var onDrop: (([URL]) -> Void)?
    var onRemove: (([URL]) -> Void)?

    private let panel = KeyPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                                 backing: .buffered, defer: true)
    private let root = DropView()
    private let titleLabel = NSTextField(labelWithString: "Bandeja")
    private let emptyLabel = NSTextField(labelWithString: "Suelta archivos aquí para guardarlos")
    private let scroll = NSScrollView()
    private let grid = FlippedView()
    private let clearButton = NSButton(title: "Vaciar bandeja", target: nil, action: nil)
    private var anchor: NSRect = .zero  // screen rect of the shelf tile
    private var dragging: URL?
    private var previousApp: NSRunningApplication?  // gets focus back on Esc

    var isOpen: Bool { panel.isVisible }

    override init() {
        super.init()
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.dockWindow)) + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .ignoresCycle, .transient]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        panel.onCancel = { [weak self] in
            self?.close()
            self?.previousApp?.activate()
        }
        installGlass(in: panel, content: root)

        root.source = self
        root.onDrop = { [weak self] urls in self?.onDrop?(urls) }
        root.registerForDraggedTypes([.fileURL])

        titleLabel.font = .systemFont(ofSize: 15, weight: .medium)
        titleLabel.alignment = .center
        emptyLabel.font = .systemFont(ofSize: 13)
        emptyLabel.textColor = .secondaryLabelColor
        emptyLabel.alignment = .center

        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.documentView = grid

        clearButton.bezelStyle = .rounded
        clearButton.controlSize = .regular
        clearButton.target = self
        clearButton.action = #selector(clearAll)
        [titleLabel, emptyLabel, scroll, clearButton].forEach(root.addSubview)
    }

    // MARK: - Showing

    func toggle(from tile: NSView) {
        if isOpen {
            close()
            return
        }
        guard let window = tile.window else { return }
        anchor = window.convertToScreen(tile.convert(tile.bounds, to: nil))
        reload()
        panel.alphaValue = 0
        previousApp = NSWorkspace.shared.frontmostApplication
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.15
            panel.animator().alphaValue = 1
        }
    }

    func close() {
        guard isOpen else { return }
        panel.orderOut(nil)
    }

    func windowDidResignKey(_ notification: Notification) { close() }

    /// Rebuilds the grid; call whenever the shelf or a thumbnail changes.
    func reload() {
        let urls = files()
        grid.subviews.forEach { $0.removeFromSuperview() }

        let columns = max(1, min(urls.count, maxColumns))
        let rows = (urls.count + columns - 1) / columns
        let gridW = CGFloat(columns) * cell.width
        let gridH = CGFloat(rows) * cell.height
        let viewH = urls.isEmpty ? 90 : CGFloat(min(rows, maxVisibleRows)) * cell.height
        let scroller: CGFloat = rows > maxVisibleRows ? 16 : 0
        let width = max(gridW + scroller + margin * 2, 320)
        let height = titleHeight + viewH + footerHeight

        for (i, url) in urls.enumerated() {
            let item = GridItem(url: url, image: thumbnail(url), owner: self)
            item.setFrameOrigin(NSPoint(x: CGFloat(i % columns) * cell.width,
                                        y: CGFloat(i / columns) * cell.height))
            grid.addSubview(item)
        }
        grid.frame = NSRect(x: 0, y: 0, width: gridW, height: gridH)

        titleLabel.frame = NSRect(x: 0, y: height - titleHeight + 4, width: width, height: 24)
        scroll.frame = NSRect(x: ((width - gridW - scroller) / 2).rounded(), y: footerHeight,
                              width: gridW + scroller, height: viewH)
        scroll.isHidden = urls.isEmpty
        emptyLabel.isHidden = !urls.isEmpty
        emptyLabel.frame = NSRect(x: 0, y: footerHeight + viewH / 2 - 10, width: width, height: 20)
        clearButton.sizeToFit()
        clearButton.setFrameOrigin(NSPoint(x: ((width - clearButton.frame.width) / 2).rounded(), y: 10))
        clearButton.isEnabled = !urls.isEmpty

        // above the tile, centered on it, kept on screen
        let screen = NSScreen.screens.first { $0.frame.intersects(anchor) } ?? NSScreen.main
        let bounds = screen?.visibleFrame ?? .zero
        var x = (anchor.midX - width / 2).rounded()
        x = min(max(x, bounds.minX + 8), bounds.maxX - 8 - width)
        let frame = NSRect(x: x, y: anchor.maxY + 12, width: width, height: height)
        panel.setFrame(frame, display: true)
        root.frame = NSRect(origin: .zero, size: frame.size)
        setGlassRadius(panel, 22)
    }

    // MARK: - Actions

    fileprivate func open(_ url: URL) {
        close()
        NSWorkspace.shared.open(url)
    }

    fileprivate func reveal(_ url: URL) {
        close()
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func clearAll() {
        onRemove?(files())
    }

    // MARK: - Drag out

    fileprivate func beginDrag(_ item: GridItem, event: NSEvent) {
        dragging = item.url
        let drag = NSDraggingItem(pasteboardWriter: item.url as NSURL)
        drag.setDraggingFrame(item.thumb.frame, contents: item.thumb.image)
        item.beginDraggingSession(with: [drag], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession,
                         sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .outsideApplication ? [.copy, .move, .generic] : []
    }

    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint,
                         operation: NSDragOperation) {
        if let url = dragging, !operation.isEmpty { onRemove?([url]) }
        dragging = nil
    }
}
