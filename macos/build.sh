#!/bin/bash
# Builds macos/build/ATR-TASKBAR.app (needs Xcode Command Line Tools).
#   ./build.sh            build
#   ./build.sh --install  build and copy to /Applications
set -euo pipefail
cd "$(dirname "$0")"

APP="build/ATR-TASKBAR.app"
ARCH="$(uname -m)"

echo "[1/4] Compiling ($ARCH)..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -O -swift-version 5 -target "$ARCH-apple-macos13.0" \
    -o "$APP/Contents/MacOS/ATR-TASKBAR" Sources/*.swift

echo "[2/4] Bundling resources..."
cp Info.plist "$APP/Contents/Info.plist"
cp ../logo/logo.png "$APP/Contents/Resources/logo.png"

echo "[3/4] Generating icon..."
ICONSET="build/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
    sips -z $s $s ../logo/logo.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) ../logo/logo.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

echo "[4/4] Signing (ad-hoc)..."
codesign --force --sign - "$APP"

if [[ "${1:-}" == "--install" ]]; then
    pkill -x ATR-TASKBAR 2>/dev/null || true
    rm -rf /Applications/ATR-TASKBAR.app
    cp -R "$APP" /Applications/
    echo "Installed: /Applications/ATR-TASKBAR.app"
    open /Applications/ATR-TASKBAR.app
else
    echo "Done: macos/$APP"
fi
