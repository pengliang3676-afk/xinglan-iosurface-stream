from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from typing import Any


HOST_CLASS_NAME = "XinglanNativeImeHost"
HOST_WIDTH = 12
HOST_HEIGHT = 24
OWNER_RIGHT_INSET = 390
OWNER_BOTTOM_INSET = 150
FOLLOW_TIMER_MS = 30

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

SW_SHOW = 5
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GWLP_WNDPROC = -4
GA_ROOT = 2

WM_DESTROY = 0x0002
WM_SETFOCUS = 0x0007
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_KEYDOWN = 0x0100
WM_IME_STARTCOMPOSITION = 0x010D
WM_IME_ENDCOMPOSITION = 0x010E
WM_CTLCOLOREDIT = 0x0133
WM_APP = 0x8000
WM_APP_FLUSH = WM_APP + 1
WM_APP_FOCUS = WM_APP + 2

VK_BACK = 0x08
VK_RETURN = 0x0D
EN_CHANGE = 0x0300
EDIT_CONTROL_ID = 1001
EM_SETMARGINS = 0x00D3
EC_LEFTMARGIN = 0x0001
EC_RIGHTMARGIN = 0x0002

CFS_FORCE_POSITION = 0x0020
CFS_CANDIDATEPOS = 0x0040
GCS_COMPSTR = 0x0008

COLOR_WINDOW = 5
DEFAULT_GUI_FONT = 17


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class COMPOSITIONFORM(ctypes.Structure):
    _fields_ = [
        ("dwStyle", ctypes.c_uint32),
        ("ptCurrentPos", POINT),
        ("rcArea", RECT),
    ]


class CANDIDATEFORM(ctypes.Structure):
    _fields_ = [
        ("dwIndex", ctypes.c_uint32),
        ("dwStyle", ctypes.c_uint32),
        ("ptCurrentPos", POINT),
        ("rcArea", RECT),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


def emit(kind: str, *values: Any) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(json.dumps([kind, *values], ensure_ascii=True) + "\n")
        stream.flush()
    except (BrokenPipeError, OSError):
        return


def native_host_target(
    owner: RECT | None,
    fallback_x: int,
    fallback_y: int,
) -> tuple[int, int]:
    """Return a real caret position in the Xinglan window's lower-right area."""

    if owner is None:
        return int(fallback_x), int(fallback_y)
    x = max(int(owner.left) + 20, int(owner.right) - OWNER_RIGHT_INSET)
    y = max(int(owner.top) + 40, int(owner.bottom) - OWNER_BOTTOM_INSET)
    return x, y


_HOSTS: dict[int, "NativeImeHost"] = {}
_EDIT_HOSTS: dict[int, "NativeImeHost"] = {}


@WNDPROC
def _host_window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
    host = _HOSTS.get(int(hwnd))
    if host is not None:
        result = host.handle_host_message(message, wparam, lparam)
        if result is not None:
            return int(result)
    return int(ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam))


@WNDPROC
def _edit_window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
    host = _EDIT_HOSTS.get(int(hwnd))
    if host is None or not host.original_edit_proc:
        return int(ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam))
    return int(host.handle_edit_message(message, wparam, lparam))


class NativeImeHost:
    """A standard Win32 EDIT that gives TSF/IMM a genuine caret rectangle."""

    def __init__(self, fallback_x: int, fallback_y: int, owner_hwnd: int) -> None:
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.gdi32 = ctypes.windll.gdi32
        self.imm32 = ctypes.windll.imm32
        self.fallback_x = int(fallback_x)
        self.fallback_y = int(fallback_y)
        self.owner_hwnd = self._root_handle(int(owner_hwnd))
        self.instance = 0
        self.host_hwnd = 0
        self.edit_hwnd = 0
        self.original_edit_proc = 0
        self.composing = False
        self.setting_text = False
        self.ready_emitted = False
        self.last_position: tuple[int, int] | None = None
        self.background_brush = 0
        self._configure_api()

    def _configure_api(self) -> None:
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi32.GetStockObject.restype = wintypes.HANDLE
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self.user32.LoadCursorW.restype = wintypes.HANDLE
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallWindowProcW.restype = LRESULT
        self.user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_ssize_t,
        ]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.SetTimer.argtypes = [
            wintypes.HWND,
            ctypes.c_size_t,
            wintypes.UINT,
            ctypes.c_void_p,
        ]
        self.user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        self.user32.HideCaret.argtypes = [wintypes.HWND]
        self.imm32.ImmGetContext.argtypes = [wintypes.HWND]
        self.imm32.ImmGetContext.restype = wintypes.HANDLE
        self.imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        self.imm32.ImmGetCompositionStringW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        self.imm32.ImmSetCompositionWindow.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(COMPOSITIONFORM),
        ]
        self.imm32.ImmSetCandidateWindow.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(CANDIDATEFORM),
        ]

    def _root_handle(self, handle: int) -> int:
        if not handle:
            return 0
        root = self.user32.GetAncestor(wintypes.HWND(handle), GA_ROOT)
        return int(root) if root else int(handle)

    def _register_host_class(self) -> None:
        self.instance = int(self.kernel32.GetModuleHandleW(None))
        self.background_brush = int(self.gdi32.CreateSolidBrush(0x00151E2C))
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = _host_window_proc
        window_class.hInstance = wintypes.HINSTANCE(self.instance)
        window_class.hCursor = self.user32.LoadCursorW(
            None, ctypes.cast(ctypes.c_void_p(32513), wintypes.LPCWSTR)
        )
        window_class.hbrBackground = wintypes.HBRUSH(self.background_brush)
        window_class.lpszClassName = HOST_CLASS_NAME
        atom = self.user32.RegisterClassW(ctypes.byref(window_class))
        if not atom and ctypes.get_last_error() not in (0, 1410):
            raise ctypes.WinError(ctypes.get_last_error())

    def _owner_rect(self) -> RECT | None:
        if not self.owner_hwnd or not self.user32.IsWindow(wintypes.HWND(self.owner_hwnd)):
            return None
        rect = RECT()
        if not self.user32.GetWindowRect(wintypes.HWND(self.owner_hwnd), ctypes.byref(rect)):
            return None
        return rect

    def _create_windows(self) -> None:
        self._register_host_class()
        x, y = native_host_target(self._owner_rect(), self.fallback_x, self.fallback_y)
        owner = wintypes.HWND(self.owner_hwnd) if self.owner_hwnd else None
        host = self.user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
            HOST_CLASS_NAME,
            "",
            WS_POPUP | WS_VISIBLE,
            x,
            y,
            HOST_WIDTH,
            HOST_HEIGHT,
            owner,
            None,
            wintypes.HINSTANCE(self.instance),
            None,
        )
        if not host:
            raise ctypes.WinError(ctypes.get_last_error())
        self.host_hwnd = int(host)
        _HOSTS[self.host_hwnd] = self

        edit = self.user32.CreateWindowExW(
            0,
            "EDIT",
            "",
            WS_CHILD | WS_VISIBLE | ES_LEFT | ES_AUTOHSCROLL,
            0,
            0,
            HOST_WIDTH,
            HOST_HEIGHT,
            wintypes.HWND(self.host_hwnd),
            ctypes.c_void_p(EDIT_CONTROL_ID),
            wintypes.HINSTANCE(self.instance),
            None,
        )
        if not edit:
            raise ctypes.WinError(ctypes.get_last_error())
        self.edit_hwnd = int(edit)
        _EDIT_HOSTS[self.edit_hwnd] = self
        self.original_edit_proc = int(
            self.user32.SetWindowLongPtrW(
                wintypes.HWND(self.edit_hwnd),
                GWLP_WNDPROC,
                ctypes.cast(_edit_window_proc, ctypes.c_void_p).value,
            )
        )
        if not self.original_edit_proc:
            raise ctypes.WinError(ctypes.get_last_error())
        font = self.gdi32.GetStockObject(DEFAULT_GUI_FONT)
        self.user32.SendMessageW(wintypes.HWND(self.edit_hwnd), 0x0030, font, True)
        self.user32.SendMessageW(
            wintypes.HWND(self.edit_hwnd),
            EM_SETMARGINS,
            EC_LEFTMARGIN | EC_RIGHTMARGIN,
            0,
        )
        self.user32.SetTimer(wintypes.HWND(self.host_hwnd), 1, FOLLOW_TIMER_MS, None)
        self.last_position = (x, y)

    def position_host(self, *, force_ime: bool = False) -> None:
        if not self.host_hwnd:
            return
        owner = self._owner_rect()
        if self.owner_hwnd and owner is None:
            self.user32.DestroyWindow(wintypes.HWND(self.host_hwnd))
            return
        target = native_host_target(owner, self.fallback_x, self.fallback_y)
        if target != self.last_position:
            self.user32.SetWindowPos(
                wintypes.HWND(self.host_hwnd),
                ctypes.c_void_p(-1),
                target[0],
                target[1],
                HOST_WIDTH,
                HOST_HEIGHT,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            self.last_position = target
            force_ime = True
        if force_ime:
            self.apply_ime_anchor()

    def apply_ime_anchor(self) -> bool:
        if not self.edit_hwnd:
            return False
        edit = wintypes.HWND(self.edit_hwnd)
        context = self.imm32.ImmGetContext(edit)
        if not context:
            return False
        try:
            composition = COMPOSITIONFORM(
                CFS_FORCE_POSITION,
                POINT(1, 2),
                RECT(0, 0, HOST_WIDTH, HOST_HEIGHT),
            )
            candidate = CANDIDATEFORM(
                0,
                CFS_CANDIDATEPOS,
                POINT(1, HOST_HEIGHT),
                RECT(0, 0, HOST_WIDTH, HOST_HEIGHT),
            )
            composition_ok = bool(
                self.imm32.ImmSetCompositionWindow(context, ctypes.byref(composition))
            )
            candidate_ok = bool(
                self.imm32.ImmSetCandidateWindow(context, ctypes.byref(candidate))
            )
            return composition_ok or candidate_ok
        finally:
            self.imm32.ImmReleaseContext(edit, context)

    def focus_edit(self) -> None:
        if not self.host_hwnd or not self.edit_hwnd:
            return
        self.position_host(force_ime=True)
        self.user32.ShowWindow(wintypes.HWND(self.host_hwnd), SW_SHOW)
        self.user32.SetForegroundWindow(wintypes.HWND(self.host_hwnd))
        self.user32.SetActiveWindow(wintypes.HWND(self.host_hwnd))
        self.user32.SetFocus(wintypes.HWND(self.edit_hwnd))
        self.apply_ime_anchor()
        if not self.ready_emitted:
            self.ready_emitted = True
            emit("ready")

    def flush_committed_text(self) -> None:
        if self.composing or self.setting_text or not self.edit_hwnd:
            return
        edit = wintypes.HWND(self.edit_hwnd)
        length = int(self.user32.GetWindowTextLengthW(edit))
        if length <= 0:
            return
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(edit, buffer, length + 1)
        text = buffer.value
        if not text:
            return
        self.setting_text = True
        try:
            self.user32.SetWindowTextW(edit, "")
        finally:
            self.setting_text = False
        emit("text", text)

    def _call_original_edit(self, message: int, wparam: int, lparam: int) -> int:
        return int(
            self.user32.CallWindowProcW(
                ctypes.c_void_p(self.original_edit_proc),
                wintypes.HWND(self.edit_hwnd),
                message,
                wparam,
                lparam,
            )
        )

    def ime_composition_active(self) -> bool:
        if self.composing or not self.edit_hwnd:
            return self.composing
        edit = wintypes.HWND(self.edit_hwnd)
        context = self.imm32.ImmGetContext(edit)
        if not context:
            return False
        try:
            return int(
                self.imm32.ImmGetCompositionStringW(
                    context, GCS_COMPSTR, None, 0
                )
            ) > 0
        finally:
            self.imm32.ImmReleaseContext(edit, context)

    def handle_edit_message(self, message: int, wparam: int, lparam: int) -> int:
        if message == WM_SETFOCUS:
            result = self._call_original_edit(message, wparam, lparam)
            # Keep the real Win32 caret and its geometry for TSF/Sogou, but do
            # not paint the thin white caret over Xinglan's control divider.
            self.user32.HideCaret(wintypes.HWND(self.edit_hwnd))
            self.apply_ime_anchor()
            return result
        if message == WM_IME_STARTCOMPOSITION:
            self.composing = True
            self.apply_ime_anchor()
            return self._call_original_edit(message, wparam, lparam)
        if message == WM_IME_ENDCOMPOSITION:
            result = self._call_original_edit(message, wparam, lparam)
            self.composing = False
            self.user32.PostMessageW(
                wintypes.HWND(self.host_hwnd), WM_APP_FLUSH, 0, 0
            )
            return result
        if message == WM_KEYDOWN and not self.ime_composition_active():
            if int(wparam) == VK_RETURN:
                emit("key", 0x07, 0x28, "回车")
                return 0
            if int(wparam) == VK_BACK:
                emit("key", 0x07, 0x2A, "退格")
                return 0
        return self._call_original_edit(message, wparam, lparam)

    def handle_host_message(
        self, message: int, wparam: int, lparam: int
    ) -> int | None:
        if message == WM_COMMAND:
            notification = (int(wparam) >> 16) & 0xFFFF
            control_id = int(wparam) & 0xFFFF
            if control_id == EDIT_CONTROL_ID and notification == EN_CHANGE:
                if not self.composing and not self.setting_text:
                    self.user32.PostMessageW(
                        wintypes.HWND(self.host_hwnd), WM_APP_FLUSH, 0, 0
                    )
                return 0
        elif message == WM_TIMER:
            self.position_host()
            return 0
        elif message == WM_APP_FLUSH:
            self.flush_committed_text()
            return 0
        elif message in (WM_APP_FOCUS, WM_SETFOCUS):
            self.focus_edit()
            return 0
        elif message == WM_CTLCOLOREDIT:
            self.gdi32.SetTextColor(wintypes.HDC(wparam), 0x00151E2C)
            self.gdi32.SetBkColor(wintypes.HDC(wparam), 0x00151E2C)
            return int(self.background_brush)
        elif message == WM_CLOSE:
            self.user32.DestroyWindow(wintypes.HWND(self.host_hwnd))
            return 0
        elif message == WM_DESTROY:
            self.user32.PostQuitMessage(0)
            return 0
        return None

    def run(self) -> None:
        self._create_windows()
        self.user32.PostMessageW(
            wintypes.HWND(self.host_hwnd), WM_APP_FOCUS, 0, 0
        )
        message = MSG()
        try:
            while self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self.close()

    def close(self) -> None:
        if self.host_hwnd:
            try:
                self.user32.KillTimer(wintypes.HWND(self.host_hwnd), 1)
            except (AttributeError, OSError):
                pass
        _EDIT_HOSTS.pop(self.edit_hwnd, None)
        _HOSTS.pop(self.host_hwnd, None)
        self.edit_hwnd = 0
        self.host_hwnd = 0
        if self.background_brush:
            self.gdi32.DeleteObject(wintypes.HGDIOBJ(self.background_brush))
            self.background_brush = 0


def run_native_ime_worker(
    screen_x: int,
    screen_y: int,
    owner_hwnd: int = 0,
) -> None:
    NativeImeHost(screen_x, screen_y, owner_hwnd).run()
