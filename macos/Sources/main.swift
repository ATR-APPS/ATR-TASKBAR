// ATR-TASKBAR for macOS: menu bar app that shows what is playing (Spotify /
// YouTube) at the bottom-left of the screen, next to the Dock.

import AppKit
import ServiceManagement

private let appName = "ATR-TASKBAR"
private let defaults = UserDefaults.standard

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let watcher = MediaWatcher()
    private let widget = MediaWidget()
    private var statusItem: NSStatusItem!
    private var mediaItem: NSMenuItem!
    private var loginItem: NSMenuItem!
    private var geometryTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let others = NSRunningApplication.runningApplications(
            withBundleIdentifier: Bundle.main.bundleIdentifier ?? ""
        ).filter { $0 != .current }
        if !others.isEmpty {
            NSApp.terminate(nil)
            return
        }

        defaults.register(defaults: ["showMedia": true])
        widget.enabled = defaults.bool(forKey: "showMedia")
        widget.onClick = { [weak self] in self?.watcher.toggle() }
        watcher.onChange = { [weak self] media in self?.widget.show(media) }
        watcher.start()

        setupStatusItem()
        addToLoginItemsOnFirstRun()

        // follow the Dock when it is resized, moved or the displays change
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.widget.update() }
        // the Dock posts nothing when it is resized: re-check often, it is
        // just a read of the screen frames when nothing changed
        geometryTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.widget.update()
        }
        geometryTimer?.tolerance = 0.2
    }

    // MARK: - Menu bar

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let logo = Bundle.main.image(forResource: "logo") {
            logo.size = NSSize(width: 18, height: 18)
            statusItem.button?.image = logo
        } else {
            statusItem.button?.image = NSImage(systemSymbolName: "music.note", accessibilityDescription: appName)
        }
        statusItem.button?.toolTip = appName

        let menu = NSMenu()
        menu.delegate = self
        let title = NSMenuItem(title: appName, action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        menu.addItem(.separator())
        mediaItem = menu.addItem(withTitle: "Mostrar música", action: #selector(toggleMedia), keyEquivalent: "")
        loginItem = menu.addItem(withTitle: "Iniciar con macOS", action: #selector(toggleLogin), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Salir", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        for item in [mediaItem, loginItem] { item?.target = self }
        statusItem.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        mediaItem.state = widget.enabled ? .on : .off
        loginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    @objc private func toggleMedia() {
        widget.enabled.toggle()
        defaults.set(widget.enabled, forKey: "showMedia")
    }

    @objc private func toggleLogin() {
        let service = SMAppService.mainApp
        if service.status == .enabled {
            try? service.unregister()
        } else {
            try? service.register()
        }
    }

    // like the Windows build: start with the system unless the user opts out
    private func addToLoginItemsOnFirstRun() {
        guard !defaults.bool(forKey: "loginConfigured") else { return }
        defaults.set(true, forKey: "loginConfigured")
        try? SMAppService.mainApp.register()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
