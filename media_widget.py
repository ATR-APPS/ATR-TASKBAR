"""Now-playing widget for the Windows 10 taskbar.

Reads the system media sessions (Spotify, YouTube in the browser, ...) through
GlobalSystemMediaTransportControls - the same API ATR-NOTCH uses - and draws a
square cover + title in a per-pixel-alpha layered child window embedded in
the taskbar, right after the Start / search / task view buttons.

Left click toggles play / pause.
"""

import asyncio
import ctypes
import ctypes.wintypes as wt
import io
import os
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont

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

WS_CHILD = 0x40000000
WS_CLIPSIBLINGS = 0x04000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_MOUSEACTIVATE = 0x0021
WM_APP_REFRESH = 0x8001
MA_NOACTIVATE = 3

HWND_TOP = wt.HWND(0)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
SW_HIDE = 0
ULW_ALPHA = 2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
IDC_HAND = 32649


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
    __slots__ = ("key", "title", "cover", "playing")

    def __init__(self, key, title, cover, playing):
        self.key, self.title, self.cover, self.playing = key, title, cover, playing


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

    def toggle(self):
        s = self._session
        if s is not None:
            asyncio.run_coroutine_threadsafe(self._toggle(s), self._loop)

    async def _toggle(self, s):
        try:
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
        self._publish(_Media(key, props.title, cover, playing))

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


def _render(media, height, scale):
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
    y0 = (height - cover_px) // 2
    dim = 255 if media.playing else 150

    # cover with rounded corners (supersampled mask)
    if media.cover is not None:
        cover = media.cover.resize((cover_px, cover_px), Image.LANCZOS).convert("RGBA")
    else:
        cover = Image.new("RGBA", (cover_px, cover_px), (60, 60, 60, 255))
        ImageDraw.Draw(cover).text((cover_px // 2, cover_px // 2), "♪",
                                   font=_font(cover_px // 2, ("seguisym.ttf", "segoeui.ttf")), fill=(200, 200, 200, 255),
                                   anchor="mm")
    ss = 4
    mask = Image.new("L", (cover_px * ss, cover_px * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, cover_px * ss - 1, cover_px * ss - 1],
                                           radius=round(4 * scale * ss), fill=dim)
    cover.putalpha(mask.resize((cover_px, cover_px), Image.LANCZOS))
    img.alpha_composite(cover, (0, y0))

    # title
    text_mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(text_mask).text((cover_px + gap, height // 2), title, font=font,
                                   fill=230 if media.playing else 140, anchor="lm")
    layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    layer.putalpha(text_mask)
    img.alpha_composite(layer)
    return img


# ── Window ───────────────────────────────────────────────────────────────────

class TaskbarMediaWidget:
    CLASS_NAME = "ATRTaskbarMedia"

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
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_APP_REFRESH, 0, 0)

    def _ui_thread(self):
        hinst = _kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND))
        wc.lpszClassName = self.CLASS_NAME
        _user32.RegisterClassExW(ctypes.byref(wc))

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
            _user32.SetTimer(self.hwnd, 1, 2000, None)
            self._tick()

            msg = wt.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            self.hwnd = None
            self._set_right_edge(0)
            if not self._stopping:
                time.sleep(1.0)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg in (WM_TIMER, WM_APP_REFRESH):
            self._tick()
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_LBUTTONUP:
            self._watcher.toggle()
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

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
            w = self._paint(_render(media, h, scale), x, 0)
            self._shown_key, self._geometry = state, geometry
            self._width = w
        if not self._visible:
            _user32.SetWindowPos(self.hwnd, HWND_TOP, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self._visible = True
        self._set_right_edge(tb_left + x + self._width)

    def _paint(self, img, x, y):
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
        _user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pos), ctypes.byref(size),
                                    mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)

        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(dib)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(None, screen)
        return w
