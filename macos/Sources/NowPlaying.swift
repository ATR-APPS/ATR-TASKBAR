// Now-playing source for macOS.
//
// The system MediaRemote framework is closed to third-party apps since
// macOS 15.4, so this reads the players directly through AppleScript:
// Spotify (track, artist, artwork url) and YouTube tabs in Chromium browsers
// and Safari. Play / pause state of a browser tab needs JavaScript from
// Apple Events enabled in that browser; without it the tab counts as playing.

import AppKit

struct Media: Equatable {
    let id: String          // "spotify" or "<bundle id>|<window id>|<tab ref>"
    let title: String
    let artist: String
    let artwork: URL?
    let playing: Bool
}

private let pausedLinger: TimeInterval = 60  // seconds a paused session stays visible
private let pollInterval: TimeInterval = 2   // browsers have no change events

private let spotifyID = "com.spotify.client"

private struct Browser {
    let bundleID: String
    let safari: Bool
}

private let browsers = [
    Browser(bundleID: "com.google.Chrome", safari: false),
    Browser(bundleID: "com.brave.Browser", safari: false),
    Browser(bundleID: "com.microsoft.edgemac", safari: false),
    Browser(bundleID: "com.vivaldi.Vivaldi", safari: false),
    Browser(bundleID: "company.thebrowser.Browser", safari: false),  // Arc
    Browser(bundleID: "com.apple.Safari", safari: true),
]

private let stateJS = "(function(){var v=document.querySelector('video');return v?(v.paused?'paused':'playing'):'none'})()"
private let toggleJS = "(function(){var v=document.querySelector('video');if(v){v.paused?v.play():v.pause()}})()"

private let spotifyScript = """
set sep to character id 9
tell application id "\(spotifyID)"
    if player state is stopped then return ""
    set t to current track
    set art to ""
    try
        set art to artwork url of t
    end try
    return (player state as string) & sep & (name of t) & sep & (artist of t) & sep & art
end tell
"""

/// One line per YouTube tab: window id, tab ref, url, title, state.
private func listScript(_ b: Browser) -> String {
    let title = b.safari ? "name" : "title"
    let tabRef = b.safari ? "i" : "(id of t)"
    let exec = b.safari ? "do JavaScript \"\(stateJS)\" in t" : "execute t javascript \"\(stateJS)\""
    return """
    set sep to character id 9
    set out to ""
    tell application id "\(b.bundleID)"
        repeat with w in windows
            try
                set wid to id of w
                set i to 0
                repeat with t in tabs of w
                    set i to i + 1
                    set u to URL of t
                    if u is not missing value and u contains "youtube.com/watch" then
                        set vstate to "unknown"
                        try
                            set vstate to \(exec)
                        end try
                        set out to out & wid & sep & \(tabRef) & sep & u & sep & (\(title) of t) & sep & vstate & linefeed
                    end if
                end repeat
            end try
        end repeat
    end tell
    return out
    """
}

private func toggleScript(bundleID: String, window: String, tab: String) -> String {
    if bundleID == spotifyID {
        return "tell application id \"\(spotifyID)\" to playpause"
    }
    if bundleID == "com.apple.Safari" {
        return """
        tell application id "\(bundleID)"
            do JavaScript "\(toggleJS)" in tab \(tab) of (first window whose id is \(window))
        end tell
        """
    }
    return """
    tell application id "\(bundleID)"
        set w to first window whose id is \(window)
        execute (first tab of w whose id is \(tab)) javascript "\(toggleJS)"
    end tell
    """
}

private func isRunning(_ bundleID: String) -> Bool {
    // never address an app that is not open: AppleScript would launch it
    !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty
}

private func videoID(_ url: String) -> String? {
    URLComponents(string: url)?.queryItems?.first { $0.name == "v" }?.value
}

private func cleanTitle(_ title: String) -> String {
    var t = title.replacingOccurrences(of: #"^\(\d+\)\s*"#, with: "", options: .regularExpression)
    for suffix in [" - YouTube Music", " - YouTube"] where t.hasSuffix(suffix) {
        t = String(t.dropLast(suffix.count))
        break
    }
    return t
}

final class MediaWatcher {
    /// Called on the main thread whenever the shown media changes.
    var onChange: ((Media?) -> Void)?

    private let queue = DispatchQueue(label: "atr-taskbar.media")
    private var timer: DispatchSourceTimer?
    private var scripts: [String: NSAppleScript] = [:]
    private var pausedSince: [String: Date] = [:]
    private var current: Media?

    func start() {
        // Spotify announces its changes; browsers are polled
        DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("com.spotify.client.PlaybackStateChanged"),
            object: nil, queue: nil
        ) { [weak self] _ in
            self?.queue.asyncAfter(deadline: .now() + 0.1) { self?.poll() }
        }
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now(), repeating: pollInterval, leeway: .milliseconds(500))
        t.setEventHandler { [weak self] in self?.poll() }
        t.resume()
        timer = t
    }

    func toggle() {
        queue.async {
            guard let m = self.current else { return }
            let parts = m.id.components(separatedBy: "|")
            let bundleID = parts[0] == "spotify" ? spotifyID : parts[0]
            guard isRunning(bundleID) else { return }
            let window = parts.count > 1 ? parts[1] : ""
            let tab = parts.count > 2 ? parts[2] : ""
            _ = self.run(toggleScript(bundleID: bundleID, window: window, tab: tab), cache: false)
            self.poll()
        }
    }

    // MARK: - Polling (on `queue`)

    private func run(_ source: String, cache: Bool = true) -> String? {
        var script = scripts[source]
        if script == nil {
            guard let s = NSAppleScript(source: source) else { return nil }
            var error: NSDictionary?
            guard s.compileAndReturnError(&error) else { return nil }
            if cache { scripts[source] = s }
            script = s
        }
        var error: NSDictionary?
        let result = script!.executeAndReturnError(&error)
        return error == nil ? (result.stringValue ?? "") : nil
    }

    private func candidates() -> [Media] {
        var found: [Media] = []
        if isRunning(spotifyID), let out = run(spotifyScript), !out.isEmpty {
            let f = out.components(separatedBy: "\t")
            if f.count >= 4, !f[1].isEmpty {
                found.append(Media(id: "spotify", title: f[1], artist: f[2],
                                   artwork: URL(string: f[3]), playing: f[0] == "playing"))
            }
        }
        for b in browsers where isRunning(b.bundleID) {
            guard let out = run(listScript(b)) else { continue }
            for line in out.split(separator: "\n") {
                let f = line.components(separatedBy: "\t")
                guard f.count >= 5, f[4] != "none", let vid = videoID(f[2]) else { continue }
                let music = f[2].contains("music.youtube.com")
                found.append(Media(id: "\(b.bundleID)|\(f[0])|\(f[1])",
                                   title: cleanTitle(f[3]),
                                   artist: music ? "YouTube Music" : "YouTube",
                                   artwork: URL(string: "https://i.ytimg.com/vi/\(vid)/mqdefault.jpg"),
                                   playing: f[4] != "paused"))
            }
        }
        return found
    }

    private func poll() {
        let now = Date()
        let found = candidates()

        var live: [Media] = []
        for m in found {
            if m.playing {
                pausedSince[m.id] = nil
                live.append(m)
            } else {
                let since = pausedSince[m.id] ?? now
                pausedSince[m.id] = since
                if now.timeIntervalSince(since) <= pausedLinger {
                    live.append(m)
                }
            }
        }
        let ids = Set(found.map(\.id))
        pausedSince = pausedSince.filter { ids.contains($0.key) }

        // playing first, then Spotify before browsers (list order)
        let pick = live.enumerated()
            .min { ($0.element.playing ? 0 : 1, $0.offset) < ($1.element.playing ? 0 : 1, $1.offset) }?
            .element
        if pick != current {
            current = pick
            DispatchQueue.main.async { self.onChange?(pick) }
        }
    }
}
