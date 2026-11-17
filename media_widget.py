"""Now-playing widget for the Windows 10 taskbar.

Reads the system media sessions (Spotify, YouTube in the browser, ...) through
GlobalSystemMediaTransportControls - the same API ATR-NOTCH uses - and draws a
square cover + title in a per-pixel-alpha layered child window embedded in
the taskbar, right after the Start / search / task view buttons.

Left click opens a flyout above the taskbar with the playback controls
(previous, play / pause, next).
"""

import asyncio
import ctypes
import ctypes.wintypes as wt
import io
import os
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageGrab

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    AVAILABLE = True
except Exception:  # winrt packages missing
    AVAILABLE = False


# ── Win32 ────────────────────────────────────────────────────────────────────

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_kernel32 = ctypes.windll.kernel32
_ole32 = ctypes.windll.ole32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
_user32.CreateWindowExW.restype = wt.HWND
_user32.FindWindowW.restype = wt.HWND
_user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
_user32.FindWindowExW.restype = wt.HWND
_user32.GetDC.restype = wt.HDC
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPVOID]
_user32.LoadCursorW.restype = wt.HANDLE
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateDIBSection.restype = wt.HBITMAP
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]
_kernel32.GetModuleHandleW.restype = wt.HMODULE
_user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                        wt.HDC, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                        wt.DWORD]
_gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.c_void_p,
                                    wt.HANDLE, wt.DWORD]
_user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
_user32.GetDpiForWindow.argtypes = [wt.HWND]
_user32.IsWindow.argtypes = [wt.HWND]
_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CLIPSIBLINGS = 0x04000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONUP = 0x0202
WM_MOUSEACTIVATE = 0x0021
WM_APP_REFRESH = 0x8001
MA_NOACTIVATE = 3

HWND_TOP = wt.HWND(0)
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
SW_HIDE = 0
ULW_ALPHA = 2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
IDC_HAND = 32649
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
SM_YVIRTUALSCREEN = 77
POPUP_TIMER = 2      # timer id that watches the open flyout
POPUP_POLL_MS = 80
POPUP_IDLE = 5.0     # flyout closes after this long without the cursor on it


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def _rect(hwnd):
    r = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


# ── Media state ──────────────────────────────────────────────────────────────

PAUSED_LINGER = 60.0  # seconds a paused session stays visible
POLL_FALLBACK = 5.0   # safety re-poll; changes normally arrive via WinRT events


class _Media:
    __slots__ = ("key", "title", "artist", "cover", "playing")

    def __init__(self, key, title, cover, playing, artist=""):
        self.key, self.title, self.cover, self.playing = key, title, cover, playing
        self.artist = artist


def _trim_branding(img):
    """Port of ATR-NOTCH ArtworkTrim: Spotify thumbnails carry the cover framed
    on a solid band with the logo underneath. Crop back to the square cover."""
    w, h = img.size
    if w < 16 or h < 16:
        return img
    rgb = img.convert("RGB")
    corner = rgb.getpixel((w - 1, h - 1))
    diff = ImageChops.difference(rgb, Image.new("RGB", (w, h), corner))
    r, g, b = diff.split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > 24 else 0)
    box = mask.getbbox()
    if not box:
        return img
    left, top, right = box[0], box[1], box[2] - 1
    size = right - left + 1
    framed = left > 0 or right < w - 1 or top > 0
    if not framed or top + size >= h:
        return img
    return img.crop((left, top, left + size, top + size))


def _square(img):
    w, h = img.size
    s = min(w, h)
    return img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


class _MediaWatcher:
    """Polls the media sessions on its own asyncio loop."""

    def __init__(self, on_change):
        self._on_change = on_change
        self._loop = asyncio.new_event_loop()
        self._session = None
        self._covers = {}
        self._paused_since = {}
        self._subs = {}  # app id -> (session, playback token, properties token)
        self._wake = None
        self.current = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def command(self, action):
        """action: 'prev', 'toggle' or 'next'."""
        s = self._session
        if s is not None:
            asyncio.run_coroutine_threadsafe(self._command(s, action), self._loop)

    async def _command(self, s, action):
        try:
            if action == "prev":
                await s.try_skip_previous_async()
            elif action == "next":
                await s.try_skip_next_async()
            else:
                await s.try_toggle_play_pause_async()
        except Exception:
            pass
        await self._poll()

    def _run(self):
        _ole32.CoInitializeEx(None, 0)  # MTA
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    def _notify(self, *_):
        # WinRT events arrive on arbitrary threads
        try:
            self._loop.call_soon_threadsafe(self._wake.set)
        except Exception:
            pass

    def _sync_subscriptions(self, sessions):
        current = {}
        for s in sessions:
            try:
                current[s.source_app_user_model_id or ""] = s
            except Exception:
                pass
        for app in list(self._subs):
            if app not in current:
                s, t1, t2 = self._subs.pop(app)
                try:
                    s.remove_playback_info_changed(t1)
                    s.remove_media_properties_changed(t2)
                except Exception:
                    pass
        for app, s in current.items():
            if app in self._subs:
                continue
            try:
                t1 = s.add_playback_info_changed(self._notify)
                t2 = s.add_media_properties_changed(self._notify)
                self._subs[app] = (s, t1, t2)
            except Exception:
                pass

    async def _main(self):
        self._wake = asyncio.Event()
        manager = None
        while True:
            try:
                if manager is None:
                    manager = await _SessionManager.request_async()
                    self._subs.clear()
                    try:
                        manager.add_sessions_changed(self._notify)
                    except Exception:
                        pass
                self._manager = manager
                await self._poll()
            except Exception:
                manager = None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_FALLBACK)
                await asyncio.sleep(0.1)  # coalesce event bursts
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    @staticmethod
    def _priority(app_id):
        a = app_id.lower()
        if "spotify" in a:
            return 0
        if any(b in a for b in ("chrome", "msedge", "brave", "firefox", "opera", "vivaldi",
                                "308046b0af4a39cb")):
            return 1
        return 2

    async def _poll(self):
        now = time.monotonic()
        candidates = []
        sessions = list(self._manager.get_sessions())
        self._sync_subscriptions(sessions)
        for s in sessions:
            try:
                app = s.source_app_user_model_id or ""
                playing = s.get_playback_info().playback_status == _Status.PLAYING
            except Exception:
                continue
            if playing:
                self._paused_since.pop(app, None)
            else:
                since = self._paused_since.setdefault(app, now)
                if now - since > PAUSED_LINGER:
                    continue
            candidates.append((not playing, self._priority(app), s, app, playing))

        if not candidates:
            self._session = None
            self._publish(None)
            return

        candidates.sort(key=lambda c: (c[0], c[1]))
        _, _, s, app, playing = candidates[0]
        try:
            props = await s.try_get_media_properties_async()
        except Exception:
            return
        if props is None or not props.title:
            return
        self._session = s
        key = f"{app}\x1f{props.title}\x1f{props.artist}"
        cover = self._covers.get(key)
        if cover is None and props.thumbnail is not None:
            cover = await self._load_cover(props.thumbnail)
            if cover is not None:
                if len(self._covers) > 30:
                    self._covers.clear()
                self._covers[key] = cover
        self._publish(_Media(key, props.title, cover, playing, props.artist or ""))

    async def _load_cover(self, ref):
        try:
            stream = await ref.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
            img = Image.open(io.BytesIO(bytes(buf))).convert("RGB")
            return _square(_trim_branding(img))
        except Exception:
            return None

    def _publish(self, media):
        old = self.current
        same = (media is None and old is None) or (
            media is not None and old is not None and media.key == old.key
            and media.playing == old.playing and (media.cover is None) == (old.cover is None))
        if same:
            return
        self.current = media
        self._on_change()


# ── Rendering ────────────────────────────────────────────────────────────────

_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_font_cache = {}


def _font(px, names=("seguisb.ttf", "segoeui.ttf", "arial.ttf")):
    key = (px, names)
    if key not in _font_cache:
        for name in names:
            try:
                _font_cache[key] = ImageFont.truetype(os.path.join(_FONTS_DIR, name), px)
                break
            except OSError:
                continue
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _ellipsize(text, font, max_w):
    if font.getlength(text) <= max_w:
        return text
    while text and font.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _rounded_cover(media, px, radius, alpha=255):
    if media.cover is not None:
        cover = media.cover.resize((px, px), Image.LANCZOS).convert("RGBA")
    else:
        cover = Image.new("RGBA", (px, px), (60, 60, 60, 255))
        ImageDraw.Draw(cover).text((px // 2, px // 2), "♪",
                                   font=_font(px // 2, ("seguisym.ttf", "segoeui.ttf")),
                                   fill=(200, 200, 200, 255), anchor="mm")
    ss = 4
    mask = Image.new("L", (px * ss, px * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px * ss - 1, px * ss - 1],
                                           radius=radius * ss, fill=alpha)
    cover.putalpha(mask.resize((px, px), Image.LANCZOS))
    return cover


def _text_layer(size, xy, text, font, alpha, anchor="lm"):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=font, fill=alpha, anchor=anchor)
    layer = Image.new("RGBA", size, (255, 255, 255, 0))
    layer.putalpha(mask)
    return layer


def _render(media, height, scale):
    """The compact widget that lives inside the taskbar: cover + title."""
    cover_px = max(16, round(height * 0.7))
    gap = round(10 * scale)
    pad_r = round(6 * scale)
    max_text = round(220 * scale)
    font = _font(max(10, round(13 * scale)))

    title = _ellipsize(media.title, font, max_text)
    text_w = int(font.getlength(title)) + 1
    width = cover_px + gap + text_w + pad_r

    # alpha 1 everywhere so the whole rectangle receives clicks
    img = Image.new("RGBA", (width, height), (0, 0, 0, 1))
    cover = _rounded_cover(media, cover_px, round(4 * scale), 255 if media.playing else 150)
    img.alpha_composite(cover, (0, (height - cover_px) // 2))
    img.alpha_composite(_text_layer((width, height), (cover_px + gap, height // 2), title, font,
                                    230 if media.playing else 140))
    return img


def _rounded_poly(draw, pts, r, fill):
    """Filled polygon with softly rounded corners: shrink it towards its
    centroid by r and stroke the outline with a 2r round-jointed pen."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    inner = []
    for x, y in pts:
        dx, dy = cx - x, cy - y
        d = (dx * dx + dy * dy) ** 0.5 or 1
        inner.append((x + dx / d * r * 1.6, y + dy / d * r * 1.6))
    draw.polygon(inner, fill=fill)
    draw.line(inner + [inner[0], inner[1]], fill=fill, width=round(r * 2), joint="curve")


def _draw_icon(draw, action, cx, cy, size, playing, fill):
    """macOS-style transport icons centred on (cx, cy); size = icon height."""
    h = size / 2
    r = size * 0.08
    if action == "toggle":
        if playing:  # pause: two rounded bars
            bw, gap = size * 0.3, size * 0.26
            draw.rounded_rectangle([cx - gap / 2 - bw, cy - h, cx - gap / 2, cy + h],
                                   bw * 0.35, fill=fill)
            draw.rounded_rectangle([cx + gap / 2, cy - h, cx + gap / 2 + bw, cy + h],
                                   bw * 0.35, fill=fill)
        else:  # play
            _rounded_poly(draw, [(cx - h * 0.8, cy - h), (cx - h * 0.8, cy + h),
                                 (cx + h * 0.95, cy)], r, fill)
        return
    # previous / next: two touching triangles
    tw = size * 0.86
    d = 1 if action == "next" else -1
    for k in (0, 1):
        back = cx - d * tw + d * tw * k
        _rounded_poly(draw, [(back, cy - h), (back, cy + h), (back + d * tw, cy)], r, fill)


CONTROLS = ("prev", "toggle", "next")


def _frost(backdrop, width, height, scale):
    """Heavy blur + light grey veil, like the macOS material."""
    small = (max(1, width // 8), max(1, height // 8))
    glass = backdrop.convert("RGB").resize(small, Image.BILINEAR)
    glass = glass.filter(ImageFilter.GaussianBlur(1.5))
    glass = glass.resize((width, height), Image.BICUBIC)
    glass = glass.filter(ImageFilter.GaussianBlur(max(2, round(4 * scale))))
    veil = Image.new("RGB", (width, height), (96, 96, 96))
    return Image.blend(glass, veil, 0.5).convert("RGBA")


def _render_popup(media, scale, width, height, backdrop=None, hover=None):
    """Thin frosted pill with the transport controls, shown above the widget.
    Returns (image, buttons) with buttons = [(x0, y0, x1, y1, action)]."""
    ss = 4
    radius = round(height * 0.32)

    if backdrop is not None:
        glass = _frost(backdrop, width, height, scale)
    else:
        glass = Image.new("RGBA", (width, height), (74, 74, 74, 255))
    mask = Image.new("L", (width * ss, height * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width * ss - 1, height * ss - 1],
                                           radius=radius * ss, fill=238)
    glass.putalpha(mask.resize((width, height), Image.LANCZOS))
    img = glass

    # hairline highlight on the edge
    edge = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([0, 0, width * ss - 1, height * ss - 1],
                                           radius=radius * ss, outline=(255, 255, 255, 38),
                                           width=ss)
    img.alpha_composite(edge.resize((width, height), Image.LANCZOS))

    # prev at the left, play / pause centred, next at the right (like macOS)
    icon = height * 0.32
    inset = max(height * 0.75, width * 0.17)
    centers = (inset, width / 2, width - inset)
    btn = height * 0.82
    hl = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    icons = Image.new("L", (width * ss, height * ss), 0)
    idr = ImageDraw.Draw(icons)
    buttons = []
    cy = height / 2
    bounds = [0, (centers[0] + centers[1]) / 2, (centers[1] + centers[2]) / 2, width]
    for i, action in enumerate(CONTROLS):
        cx = centers[i]
        buttons.append((round(bounds[i]), 0, round(bounds[i + 1]), height, action))
        if action == hover:
            hd.ellipse([(cx - btn / 2) * ss, (cy - btn / 2) * ss,
                        (cx + btn / 2) * ss, (cy + btn / 2) * ss], fill=(255, 255, 255, 30))
        _draw_icon(idr, action, cx * ss, cy * ss, icon * ss, media.playing, 236)
    img.alpha_composite(hl.resize((width, height), Image.LANCZOS))
    layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    layer.putalpha(icons.resize((width, height), Image.LANCZOS))
    img.alpha_composite(layer)
    return img, buttons


def _paint(hwnd, img, x, y):
    """Blit a PIL RGBA image into a layered window at (x, y)."""
    w, h = img.size
    r, g, b, a = img.convert("RGBa").split()  # premultiplied
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = w, -h
    bmi.biPlanes, bmi.biBitCount = 1, 32
    bits = ctypes.c_void_p()

    screen = _user32.GetDC(None)
    mem = _gdi32.CreateCompatibleDC(screen)
    dib = _gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = _gdi32.SelectObject(mem, dib)
    ctypes.memmove(bits, data, len(data))

    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    pos, size, src = wt.POINT(x, y), wt.SIZE(w, h), wt.POINT(0, 0)
    _user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(pos), ctypes.byref(size),
                                mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)

    _gdi32.SelectObject(mem, old)
    _gdi32.DeleteObject(dib)
    _gdi32.DeleteDC(mem)
    _user32.ReleaseDC(None, screen)
    return w


# ── Window ───────────────────────────────────────────────────────────────────

class TaskbarMediaWidget:
    CLASS_NAME = "ATRTaskbarMedia"
    POPUP_CLASS = "ATRTaskbarMediaPopup"

    def __init__(self):
        self.hwnd = None
        self.enabled = True
        self.right_edge = 0  # screen x where the widget ends (0 = hidden)
        self.on_layout = None  # called when right_edge changes
        self._watcher = _MediaWatcher(self._request_refresh)
        self._shown_key = None
        self._geometry = None
        self._visible = False
        self._stopping = False
        self._parent = None
        self._width = 0
        # flyout with the playback controls
        self._popup = None
        self._popup_open = False
        self._popup_key = None
        self._popup_rect = (0, 0, 0, 0)
        self._popup_buttons = []
        self._hover = None
        self._backdrop = None
        self._last_active = 0.0

    # public ------------------------------------------------------------------
    def start(self):
        if not AVAILABLE:
            return
        threading.Thread(target=self._ui_thread, daemon=True).start()
        self._watcher.start()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._request_refresh()

    def stop(self):
        self._stopping = True
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # internals ---------------------------------------------------------------
    def _request_refresh(self):
        self._shown_key = None
        self._popup_key = None
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_APP_REFRESH, 0, 0)

    def _register(self, name, proc, hinst):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND))
        wc.lpszClassName = name
        _user32.RegisterClassExW(ctypes.byref(wc))

    def _ui_thread(self):
        hinst = _kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._proc)
        self._popup_wndproc = WNDPROC(self._popup_proc)
        self._register(self.CLASS_NAME, self._wndproc, hinst)
        self._register(self.POPUP_CLASS, self._popup_wndproc, hinst)

        # The widget is a child window *inside* Shell_TrayWnd, so it shares the
        # taskbar's z-band: it stays visible with Start open, hides with the
        # taskbar under fullscreen apps and slides with it on auto-hide.
        # If explorer restarts the taskbar (and our child) die: recreate.
        while not self._stopping:
            taskbar = _user32.FindWindowW("Shell_TrayWnd", None)
            if not taskbar:
                time.sleep(1.0)
                continue
            self._parent = taskbar
            self._visible = False
            self._shown_key = self._geometry = None
            self.hwnd = _user32.CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE,
                self.CLASS_NAME, "ATR-TASKBAR Media", WS_CHILD | WS_CLIPSIBLINGS,
                0, 0, 1, 1, taskbar, None, hinst, None)
            if not self.hwnd:
                time.sleep(2.0)
                continue
            if not self._popup:
                self._popup = _user32.CreateWindowExW(
                    WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
                    self.POPUP_CLASS, "ATR-TASKBAR Media Controls", WS_POPUP,
                    0, 0, 1, 1, None, None, hinst, None)
            _user32.SetTimer(self.hwnd, 1, 2000, None)
            self._tick()

            msg = wt.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            self._close_popup()
            self.hwnd = None
            self._set_right_edge(0)
            if not self._stopping:
                time.sleep(1.0)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER and wparam == POPUP_TIMER:
            self._watch_popup()
            return 0
        if msg in (WM_TIMER, WM_APP_REFRESH):
            self._tick()
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_LBUTTONUP:
            if self._popup_open:
                self._close_popup()
            else:
                self._open_popup()
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg in (WM_MOUSEMOVE, WM_LBUTTONUP):
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            action = self._button_at(x, y)
            self._last_active = time.monotonic()
            if msg == WM_LBUTTONUP and action:
                self._watcher.command(action)
            elif action != self._hover:
                self._hover = action
                self._paint_popup(force=True)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _button_at(self, x, y):
        for x0, y0, x1, y1, action in self._popup_buttons:
            if x0 <= x < x1 and y0 <= y < y1:
                return action
        return None

    # flyout ------------------------------------------------------------------
    def _open_popup(self):
        if not self._popup or self._watcher.current is None:
            return
        self._popup_open = True
        self._hover = None
        self._popup_key = None
        self._backdrop = None  # grabbed on first paint, while still hidden
        self._last_active = time.monotonic()
        self._paint_popup()
        _user32.SetWindowPos(self._popup, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        _user32.SetTimer(self.hwnd, POPUP_TIMER, POPUP_POLL_MS, None)

    def _close_popup(self):
        if self.hwnd:
            _user32.KillTimer(self.hwnd, POPUP_TIMER)
        if self._popup_open and self._popup:
            _user32.ShowWindow(self._popup, SW_HIDE)
        self._popup_open = False
        self._hover = None

    def _paint_popup(self, force=False):
        media = self._watcher.current
        if not self._popup_open or media is None or not self._visible:
            self._close_popup()
            return
        tb = _rect(self._parent)
        try:
            scale = _user32.GetDpiForWindow(self._parent) / 96.0
        except Exception:
            scale = 1.0
        key = (media.key, media.playing, self._hover, tb, scale, _rect(self.hwnd))
        if key == self._popup_key and not force:
            return
        widget = _rect(self.hwnd)
        # same width as the widget, a bit thinner than the taskbar
        h = round((tb[3] - tb[1]) * 0.78)
        w = max(widget[2] - widget[0], round(h * 3.4))
        margin = round(5 * scale)
        x = max(tb[0] + margin, min(widget[0], tb[2] - w - margin))
        mon_top = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        if tb[1] - h - margin >= mon_top:
            y = tb[1] - h - margin  # taskbar at the bottom: open upwards
        else:
            y = tb[3] + margin      # taskbar at the top: open downwards
        if self._backdrop is None or self._backdrop[0] != (x, y, w, h):
            try:
                shot = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            except Exception:
                shot = None
            self._backdrop = ((x, y, w, h), shot)
        img, self._popup_buttons = _render_popup(media, scale, w, h, self._backdrop[1], self._hover)
        _paint(self._popup, img, x, y)
        self._popup_rect = (x, y, x + w, y + h)
        self._popup_key = key

    def _watch_popup(self):
        """Close the flyout on a click elsewhere or after a while without the
        cursor over it."""
        pt = wt.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        inside = any(r[0] <= pt.x < r[2] and r[1] <= pt.y < r[3]
                     for r in (self._popup_rect, _rect(self.hwnd)))
        now = time.monotonic()
        if inside:
            self._last_active = now
            return
        if self._hover is not None:
            self._hover = None
            self._paint_popup(force=True)
        if _user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000 or \
                _user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000 or \
                now - self._last_active > POPUP_IDLE:
            self._close_popup()

    # taskbar widget ----------------------------------------------------------
    def _taskbar_slot(self):
        """(x, height, scale, taskbar_left) in taskbar coords, or None."""
        tb = _rect(self._parent)
        if (tb[2] - tb[0]) < (tb[3] - tb[1]):
            return None  # vertical taskbar
        rebar = _user32.FindWindowExW(self._parent, None, "ReBarWindow32", None)
        x = (_rect(rebar)[0] if rebar else tb[0] + 48) - tb[0]
        try:
            scale = _user32.GetDpiForWindow(self._parent) / 96.0
        except Exception:
            scale = 1.0
        return x + round(8 * scale), tb[3] - tb[1], scale, tb[0]

    def _set_right_edge(self, value):
        if value != self.right_edge:
            self.right_edge = value
            if self.on_layout:
                self.on_layout()

    def _hide(self):
        self._close_popup()
        if self._visible:
            _user32.ShowWindow(self.hwnd, SW_HIDE)
            self._visible = False
        self._set_right_edge(0)

    def _tick(self):
        if not self.hwnd:
            return
        if not _user32.IsWindow(self._parent):
            _user32.DestroyWindow(self.hwnd)
            return
        media = self._watcher.current
        slot = self._taskbar_slot() if (self.enabled and media is not None) else None
        if slot is None:
            self._hide()
            return

        x, h, scale, tb_left = slot
        geometry = (x, h, scale)
        state = (media.key, media.playing, media.cover is not None)
        if state != self._shown_key or geometry != self._geometry:
            w = _paint(self.hwnd, _render(media, h, scale), x, 0)
            self._shown_key, self._geometry = state, geometry
            self._width = w
        if not self._visible:
            _user32.SetWindowPos(self.hwnd, HWND_TOP, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self._visible = True
        self._set_right_edge(tb_left + x + self._width)
        if self._popup_open:
            self._paint_popup()
