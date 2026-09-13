from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import logging
import multiprocessing
import os
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from xinglan.bootstrap import PROJECT_DIR, configure_dependencies

configure_dependencies()

from PIL import Image, ImageDraw, ImageFont, ImageTk  # noqa: E402

from xinglan.device_discovery import discover_usb_udids_stable  # noqa: E402
from xinglan.device_actions import (  # noqa: E402
    OnDemandDeviceActionHub,
    identify_physical_device,
)
from xinglan.device_groups import DeviceGroupStore  # noqa: E402
from xinglan.file_transfer import send_file_to_devices  # noqa: E402
from xinglan.file_download import download_folder, list_directory  # noqa: E402
from xinglan.ime_worker_client import ImeWorkerClient  # noqa: E402
from xinglan.keyboard_input import map_keypress  # noqa: E402
from xinglan.music_player import MusicPlaybackError, WindowsMusicPlayer  # noqa: E402
from xinglan.diagnostics import (  # noqa: E402
    ProcessLoadSampler,
    configure_logging,
    working_set_mb,
)
from xinglan.control_protocol import SystemAction  # noqa: E402
from xinglan.session import DeviceSession  # noqa: E402
from xinglan.usb_repair import (  # noqa: E402
    UsbRepairResult,
    WindowsIphone,
    discover_windows_iphone_count,
    discover_windows_iphones,
    load_usb_topology,
    repair_usb,
    save_usb_topology,
)


WALL_COLUMNS = 5
WALL_ROWS = 2
GROUP_SIZE = WALL_COLUMNS * WALL_ROWS
TILE_VIEW_SIZE = (230, 408)
MASTER_VIEW_SIZE = (356, 667)
TOP_BAR_HEIGHT = 40
BRAND_BANNER_HEIGHT = 36
BRAND_BANNER_WIDTH = 568
BRAND_BANNER_REL_X = 0.4705
STATUS_BLOCK_WIDTH = 330
WINDOWS_USB_REFRESH_MS = 30_000
AUTO_USB_REPAIR_COOLDOWN_SECONDS = 60.0
AUTO_USB_REPAIR_MAX_ATTEMPTS = 2
MOTTO_LINE_ONE = "日日精进，久久为功；功不唐捐，玉汝于成"
MOTTO_LINE_TWO = "道阻且长，行则将至；行而不辍，未来可期"
RIGHT_PANEL_WIDTH = 360
MASTER_BORDER_COLOR = "#d58b00"
MASTER_BORDER_THICKNESS = 2
GROUP_SELECTOR_WIDTH = 80
MUSIC_TRACKS = (
    PROJECT_DIR / "assets" / "music" / "background-music.mp3",
    PROJECT_DIR / "assets" / "music" / "shenhua-qinghua.wav",
)
PHONE_HEAD_HEIGHT = 0
SIDE_RAIL_WIDTH = 44
TILE_CHECKBOX_SIZE = 32
TILE_NUMBER_FONT_SIZE = 12
MAIN_BUTTON_FONT = ("Microsoft YaHei UI", 10, "bold")
WALL_GAP = 3
DISPLAY_INTERVAL_MS = 40
E5_RENDER_PROFILE = (PROJECT_DIR / "E5_RENDER_PROFILE").is_file()
SMOOTH_RENDER_PROFILE = (PROJECT_DIR / "SMOOTH_RENDER_PROFILE").is_file()
GROUP_BUTTONS_PROFILE = (PROJECT_DIR / "GROUP_BUTTONS_PROFILE").is_file()
GROUP_ONECLICK_PROFILE = (PROJECT_DIR / "GROUP_ONECLICK_PROFILE").is_file()
GROUP_TWO_STEP_PROFILE = (PROJECT_DIR / "GROUP_TWO_STEP_PROFILE").is_file()
OTP_PASTE_PROFILE = (PROJECT_DIR / "OTP_PASTE_PROFILE").is_file()
TROLLVNC_OTP_PASTE_PROFILE = (
    PROJECT_DIR / "TROLLVNC_OTP_PASTE_PROFILE"
).is_file()
FLICKER_FREE_WALL_PROFILE = (PROJECT_DIR / "FLICKER_FREE_WALL_PROFILE").is_file()
AUTO_USB_REPAIR_PROFILE = (PROJECT_DIR / "AUTO_USB_REPAIR_PROFILE").is_file()
TILE_RESAMPLING = (
    Image.Resampling.BILINEAR
    if E5_RENDER_PROFILE or SMOOTH_RENDER_PROFILE
    else Image.Resampling.LANCZOS
)
RENDER_SLICE_INTERVAL_MS = 10
RENDER_TILES_PER_SLICE = 2
LOGGER = logging.getLogger("xinglan.app")


@dataclass
class ActiveTouchRoute:
    """Frozen recipients plus the latest coordinate for one mouse drag."""

    targets: tuple[DeviceSession, ...]
    x: float
    y: float


def load_brand_banner(master: tk.Misc) -> ImageTk.PhotoImage:
    """Load the exact legacy toolbar brand and fit it into the compact bar."""
    banner_path = PROJECT_DIR / "assets" / "legacy-brand-banner-five.png"
    with Image.open(banner_path) as source:
        banner = source.convert("RGB").resize(
            (BRAND_BANNER_WIDTH, BRAND_BANNER_HEIGHT),
            Image.Resampling.LANCZOS,
        )
    return ImageTk.PhotoImage(banner, master=master)


def load_placeholder_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a Windows font that can render the Chinese connection message."""
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for filename in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        font_path = windows_dir / "Fonts" / filename
        if not font_path.is_file():
            continue
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


SMALL_PLACEHOLDER_FONT = load_placeholder_font(13)
MASTER_PLACEHOLDER_FONT = load_placeholder_font(15)


class NativeTitleOverlay:
    """Draw centred caption text without replacing the native Windows frame."""

    WIDTH = 90
    HEIGHT = 22
    VERTICAL_OFFSET = 4
    LEFT_INSET = 22

    def __init__(self, root: tk.Tk, text: str, rel_x: float) -> None:
        self.root = root
        self.rel_x = rel_x
        self._after_id: str | None = None
        self._last_geometry: str | None = None
        self._visible = False
        self._native_styles_applied = False
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.transient(root)
        self.window.configure(bg="#f3f3f3")
        self.label = tk.Label(
            self.window,
            text=text,
            bg="#f3f3f3",
            fg="#111111",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 9),
        )
        root.bind("<Configure>", self._schedule_refresh, add="+")
        root.bind("<Map>", self._schedule_refresh, add="+")
        root.bind("<Unmap>", self._hide, add="+")
        self._schedule_refresh()

    def _hide(self, _event: tk.Event | None = None) -> None:
        try:
            self.window.withdraw()
            self._visible = False
        except tk.TclError:
            pass

    def _schedule_refresh(self, _event: tk.Event | None = None) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = self.root.after(40, self._refresh)

    def _refresh(self) -> None:
        self._after_id = None
        try:
            if self.root.state() in {"iconic", "withdrawn"}:
                self.window.withdraw()
                return
            self.root.update_idletasks()
            outer_left, outer_top, client_left, client_top, client_width = (
                self._window_metrics()
            )
            target_x = client_left + 10 + ((client_width - 20) * self.rel_x)
            caption_height = max(self.HEIGHT, client_top - outer_top)
            x = outer_left + self.LEFT_INSET
            y = round(outer_top + ((caption_height - self.HEIGHT) / 2))
            width = max(
                self.WIDTH,
                round(target_x + (self.WIDTH / 2) - x),
            )
            overlay_height = self.HEIGHT + self.VERTICAL_OFFSET
            label_x = round(target_x - x - (self.WIDTH / 2))
            geometry = f"{width}x{overlay_height}+{x}+{y}"
            if geometry != self._last_geometry:
                self.window.geometry(geometry)
                self.label.place(
                    x=label_x,
                    y=self.VERTICAL_OFFSET,
                    width=self.WIDTH,
                    height=self.HEIGHT,
                )
                self._last_geometry = geometry
            if not self._visible:
                self.window.deiconify()
                self.window.update_idletasks()
                self._apply_native_styles()
                self._visible = True
        except (OSError, tk.TclError):
            self._hide()

    def _apply_native_styles(self) -> None:
        """Keep the caption passive and owned without repeatedly raising it.

        The previous FocusIn/lift cycle ran whenever a canvas or button received
        focus, which made the caption visibly flash during phone interaction.
        An owned Windows tool window naturally stays above its owner; the native
        flags below also prevent focus activation and let mouse input pass through
        to the real title bar.
        """
        if self._native_styles_applied or os.name != "nt":
            return

        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(self.window.winfo_id())
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        parent_hwnd = user32.GetParent(hwnd)
        if parent_hwnd:
            hwnd = parent_hwnd

        gwl_exstyle = -20
        ws_ex_transparent = 0x00000020
        ws_ex_toolwindow = 0x00000080
        ws_ex_noactivate = 0x08000000
        get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
        get_window_long.restype = ctypes.c_ssize_t
        set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_window_long.restype = ctypes.c_ssize_t
        current_style = get_window_long(hwnd, gwl_exstyle)
        set_window_long(
            hwnd,
            gwl_exstyle,
            current_style | ws_ex_transparent | ws_ex_toolwindow | ws_ex_noactivate,
        )
        self._native_styles_applied = True

    def _window_metrics(self) -> tuple[int, int, int, int, int]:
        if os.name != "nt":
            client_left = self.root.winfo_rootx()
            client_top = self.root.winfo_rooty()
            return (
                client_left,
                client_top - 30,
                client_left,
                client_top,
                self.root.winfo_width(),
            )

        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(self.root.winfo_id())
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        parent_hwnd = user32.GetParent(hwnd)
        if parent_hwnd:
            hwnd = parent_hwnd
        outer = wintypes.RECT()
        client = wintypes.RECT()
        client_origin = wintypes.POINT(0, 0)
        if not user32.GetWindowRect(hwnd, ctypes.byref(outer)):
            raise OSError("GetWindowRect failed")
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            raise OSError("GetClientRect failed")
        if not user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
            raise OSError("ClientToScreen failed")
        return (
            outer.left,
            outer.top,
            client_origin.x,
            client_origin.y,
            client.right - client.left,
        )


def make_checkbox_icon(master: tk.Misc, size: int, checked: bool) -> ImageTk.PhotoImage:
    """Create a scalable green checkbox instead of the fixed Windows indicator."""
    scale = 3
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = 1 * scale
    bounds = (inset, inset, size * scale - inset - 1, size * scale - inset - 1)
    green = "#12b76a"
    draw.rounded_rectangle(
        bounds,
        radius=3 * scale,
        fill=green if checked else None,
        outline=green,
        width=2 * scale,
    )
    if checked:
        # Keep the check mark centred when the icon size changes.  The old
        # coordinates were fixed for a 20 px icon, so enlarging the box left
        # the mark stuck in its upper-left corner.
        tick_points = tuple(
            round(value * size * scale)
            for value in (0.20, 0.50, 0.40, 0.70, 0.80, 0.30)
        )
        draw.line(
            tick_points,
            fill="white",
            width=max(2, round(size * 0.10)) * scale,
            joint="curve",
        )
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image, master=master)


class DeviceTile:
    def __init__(
        self,
        owner: "XinglanApp",
        parent: tk.Widget,
        session: DeviceSession,
        index: int,
        tile_width: int,
        tile_height: int,
    ) -> None:
        self.owner = owner
        self.session = session
        self.index = index
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.last_sequence = -1
        self.dragging = False
        self.last_touch_point: tuple[float, float] | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.render_size = (0, 0)
        self.image_bounds = (0, 0, tile_width, tile_height)
        self.placeholder_text = ""
        self._connected_state: bool | None = None
        self._status_presentation: tuple[str, str] | None = None
        self._title_text: str | None = None

        # 固定卡片外框。帧率/延迟文字每秒变化时，不允许Tk重新计算卡片宽度。
        self.frame = tk.Frame(
            parent,
            bg="#1d2939",
            highlightthickness=1,
            highlightbackground="#344054",
        )
        # 复刻旧版星澜卡片：20px 标题栏在画面上方，操作栏贯穿整卡。
        # 高频单机操作放在右侧，沿用用户更熟悉的旧版视觉顺序。
        # 手机画面一直铺到卡片底部，状态不再额外占用一整行高度。
        self.title_row = tk.Frame(self.frame, bg="#050c16")
        self.title_row.place(
            x=0,
            y=0,
            relwidth=1,
            width=-SIDE_RAIL_WIDTH,
            height=PHONE_HEAD_HEIGHT,
        )
        self.title = tk.Label(
            self.title_row,
            text=f"{index + 1:02d} · {owner.device_label(session.udid)}",
            bg="#050c16",
            fg="white",
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.title.pack(fill="both", expand=True, padx=4)
        self.canvas = tk.Canvas(
            self.frame, width=tile_width, height=tile_height, bg="#050a11",
            highlightthickness=0, cursor="arrow", takefocus=True,
        )
        self.canvas.place(
            x=0,
            y=PHONE_HEAD_HEIGHT,
            relwidth=1,
            width=-SIDE_RAIL_WIDTH,
            relheight=1,
            height=-PHONE_HEAD_HEIGHT,
        )
        self.side = tk.Frame(
            self.frame, width=SIDE_RAIL_WIDTH, bg="#111827",
            highlightthickness=1, highlightbackground="#344054",
        )
        self.side.place(
            relx=1, x=-SIDE_RAIL_WIDTH, y=0,
            width=SIDE_RAIL_WIDTH, relheight=1,
        )
        self.side.pack_propagate(False)
        self.selected_var = tk.BooleanVar(
            value=self.owner.is_device_selected(self.session.udid)
        )
        self.selection_icons = {
            False: make_checkbox_icon(self.side, TILE_CHECKBOX_SIZE, False),
            True: make_checkbox_icon(self.side, TILE_CHECKBOX_SIZE, True),
        }
        self.selection_check = tk.Button(
            self.side,
            image=self.selection_icons[self.selected_var.get()],
            command=self._toggle_selection,
            bg="#111827",
            fg="white",
            activebackground="#111827",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        tk.Label(
            self.side,
            text=f"{index + 1:02d}",
            bg="#111827",
            fg="white",
            font=("Microsoft YaHei UI", TILE_NUMBER_FONT_SIZE, "bold"),
        ).pack(fill="x", padx=3, pady=(2, 0), ipady=2)
        self.selection_check.pack(fill="x", padx=3, pady=(0, 5))
        self.master_button = tk.Button(
            self.side,
            text="主控",
            command=lambda: self.owner.select_master(self.session),
            bg="#344054",
            fg="white",
            activebackground="#d92d20",
            activeforeground="white",
            relief="flat",
            padx=2,
            pady=4,
            font=MAIN_BUTTON_FONT,
        )
        self.master_button.pack(fill="x", padx=3, pady=(0, 7))
        self.start_button = tk.Button(
            self.side, text="开始",
            command=lambda: self.owner.start_device(self.session.udid),
            bg="#1d2939", fg="white", relief="flat",
            padx=1, pady=4, font=MAIN_BUTTON_FONT,
        )
        self.start_button.pack(fill="x", padx=3, pady=2)
        self.stop_button = tk.Button(
            self.side, text="停止",
            command=lambda: self.owner.stop_device(self.session.udid),
            bg="#1d2939", fg="white", relief="flat",
            padx=1, pady=4, font=MAIN_BUTTON_FONT,
        )
        self.stop_button.pack(fill="x", padx=3, pady=2)
        self.single_action_buttons: list[tk.Button] = []
        for text, command in (
            (
                "主屏",
                lambda: self.owner.route_single_system_action(
                    self.session, SystemAction.HOME
                ),
            ),
            (
                "切换",
                lambda: self.owner.route_single_system_action(
                    self.session, SystemAction.APP_SWITCHER
                ),
            ),
            (
                "控制",
                lambda: self.owner.open_single_control_center(self.session),
            ),
        ):
            button = tk.Button(
                self.side,
                text=text,
                command=command,
                bg="#344054",
                fg="white",
                activebackground="#2e90fa",
                activeforeground="white",
                relief="flat",
                padx=1,
                pady=4,
                font=MAIN_BUTTON_FONT,
            )
            button.pack(fill="x", padx=3, pady=2)
            self.single_action_buttons.append(button)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.status = tk.Label(
            self.frame, text="等待画面", bg="#1d2939", fg="#98a2b3",
            anchor="w", width=24, font=("Microsoft YaHei UI", 8)
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Control-v>", self._paste)
        self.canvas.bind("<Control-V>", self._paste)
        self.canvas.bind("<KeyPress>", self._key_press)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.set_connected(self.owner.is_session_active(self.session.udid))
        self._show_placeholder(
            "正在连接" if self.owner.is_session_active(self.session.udid) else "等待手动投屏"
        )

    def _toggle_selection(self) -> None:
        selected = not self.selected_var.get()
        self.set_selected(selected)
        self.owner.set_device_selected(self.session.udid, selected)

    def set_selected(self, selected: bool) -> None:
        self.selected_var.set(selected)
        self.selection_check.configure(image=self.selection_icons[selected])

    def grid(self, row: int, column: int) -> None:
        self.frame.grid(
            row=row,
            column=column,
            padx=(0, WALL_GAP if column < WALL_COLUMNS - 1 else 0),
            pady=(0, WALL_GAP if row < WALL_ROWS - 1 else 0),
            sticky="nsew",
        )

    def destroy(self) -> None:
        self.canvas.delete("all")
        self.photo = None
        self.frame.destroy()

    def set_master(self, active: bool) -> None:
        color = "#d92d20" if active else "#344054"
        border = "#d92d20" if active else "#344054"
        self.master_button.configure(bg=color)
        self.frame.configure(
            highlightbackground=border,
            highlightthickness=2 if active else 1,
        )

    def set_connected(self, connected: bool) -> None:
        if connected == getattr(self, "_connected_state", None):
            return
        self._connected_state = connected
        self.start_button.configure(state="disabled" if connected else "normal")
        self.stop_button.configure(state="normal" if connected else "disabled")
        self.master_button.configure(state="normal" if connected else "disabled")
        for button in self.single_action_buttons:
            button.configure(state="normal" if connected else "disabled")

    def refresh(self) -> None:
        sequence, _, image = self.session.latest.snapshot()
        stats = self.session.stats()
        if stats.status.startswith("投屏中"):
            status_text = f"重连{stats.reconnects}"
        else:
            status_text = stats.status
        status_color = "#12b76a" if stats.status.startswith("投屏中") else "#f79009"
        status_presentation = (status_text, status_color)
        if status_presentation != getattr(self, "_status_presentation", None):
            self.status.configure(text=status_text, fg=status_color)
            self._status_presentation = status_presentation
        self.set_connected(self.owner.is_session_active(self.session.udid))
        title_text = (
            f"{self.index + 1:02d} · "
            f"{self.owner.device_label(self.session.udid)} · {status_text}"
        )
        if title_text != getattr(self, "_title_text", None):
            self.title.configure(text=title_text)
            self._title_text = title_text
        canvas_size = (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        if image is None:
            if sequence != self.last_sequence:
                self.last_sequence = sequence
                self._show_placeholder(
                    stats.status
                    if self.owner.is_session_active(self.session.udid)
                    else "等待手动投屏"
                )
            return
        if sequence == self.last_sequence and canvas_size == self.render_size:
            return
        self.last_sequence = sequence
        self._show_image(image)

    def _show_placeholder(self, text: str) -> None:
        self.placeholder_text = text
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width <= 1 or height <= 1:
            width, height = self.tile_width, self.tile_height
        self.render_size = (width, height)
        self.image_bounds = (0, 0, width, height)
        image = Image.new("RGB", (width, height), "#101828")
        draw = ImageDraw.Draw(image)
        draw.text(
            (width // 2, height // 2),
            text,
            fill="#98a2b3",
            anchor="mm",
            font=SMALL_PLACEHOLDER_FONT,
        )
        self._set_photo(image)

    def _canvas_resized(self, event: tk.Event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        _, _, image = self.session.latest.snapshot()
        if image is None:
            self._show_placeholder(self.placeholder_text)
        else:
            self.last_sequence = -1

    def _set_photo(self, image: Image.Image) -> None:
        """Reuse the Tk image buffer to avoid creating hundreds of GDI objects/sec."""
        if (
            self.photo is not None
            and self.photo.width() == image.width
            and self.photo.height() == image.height
        ):
            self.photo.paste(image)
        else:
            self.photo = ImageTk.PhotoImage(image)
            self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _show_image(self, image: Image.Image) -> None:
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        self.render_size = (canvas_width, canvas_height)
        # 小窗口优先填满卡片画布，避免手机画面与右侧操作栏之间留下黑边。
        target_size = (canvas_width, canvas_height)
        resized = image if image.size == target_size else image.resize(
            target_size, TILE_RESAMPLING
        )
        self.image_bounds = (0, 0, canvas_width, canvas_height)
        self._set_photo(resized)

    def _normalized(self, event: tk.Event) -> tuple[float, float] | None:
        left, top, right, bottom = self.image_bounds
        if event.x < left or event.x >= right or event.y < top or event.y >= bottom:
            return None
        return (event.x - left) / (right - left), (event.y - top) / (bottom - top)

    def _press(self, event: tk.Event) -> None:
        point = self._normalized(event)
        if point is None:
            return
        # Cover the short helper start-up interval: Tk canvases do not
        # reliably take keyboard focus merely because they were clicked.
        self.canvas.focus_set()
        self.dragging = True
        self.last_touch_point = point
        self.owner.route_touch(self.session, 1, *point, from_master=False)

    def _key_press(self, event: tk.Event) -> str | None:
        if event.state & 0x0004 and event.keysym.lower() == "v":
            return self._paste(event)
        return self.owner.route_keypress(
            self.session, event.keysym, event.char, from_master=False
        )

    def _paste(self, _event: tk.Event) -> str:
        self.owner.route_clipboard_paste(self.session, from_master=False)
        return "break"

    def _move(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        point = self._normalized(event)
        if point is not None:
            self.last_touch_point = point
            # Preserve every Tk coordinate. DeviceSession performs noVNC's
            # trailing-latest 17 ms scheduling on its asyncio thread.
            self.owner.route_touch(self.session, 2, *point, from_master=False)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        self.dragging = False
        # Match noVNC: use the real release coordinate when it remains on the
        # image; otherwise release at the last valid point to avoid a stuck
        # contact after dragging outside the canvas.
        point = self._normalized(event) or self.last_touch_point
        self.last_touch_point = None
        if point is not None:
            self.owner.route_touch(self.session, 0, *point, from_master=False)
        self.owner.activate_ime(
            self.session,
            from_master=False,
            screen_x=event.x_root,
            screen_y=event.y_root,
        )


class EmptySlot:
    def __init__(
        self,
        parent: tk.Widget,
        index: int,
        assigned_label: str = "",
    ) -> None:
        self.index = index
        self.assigned_label = assigned_label
        self.frame = tk.Frame(
            parent,
            bg="#050a11",
            highlightthickness=1,
            highlightbackground="#344054",
        )
        tk.Label(
            self.frame,
            text=(
                f"{index + 1:02d} · {assigned_label}"
                if assigned_label
                else f"位置 {index + 1:02d}"
            ),
            bg="#111827",
            fg="#667085",
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).place(
            x=0, y=0, relwidth=1, width=-SIDE_RAIL_WIDTH,
            height=PHONE_HEAD_HEIGHT,
        )
        tk.Label(
            self.frame,
            text="USB未连接" if assigned_label else "未分配",
            bg="#050a11",
            fg="#667085",
            font=("Microsoft YaHei UI", 10),
        ).place(
            x=0, y=PHONE_HEAD_HEIGHT,
            relwidth=1, width=-SIDE_RAIL_WIDTH,
            relheight=1, height=-PHONE_HEAD_HEIGHT,
        )
        side = tk.Frame(
            self.frame, width=SIDE_RAIL_WIDTH, bg="#111827",
            highlightthickness=1, highlightbackground="#344054",
        )
        side.place(
            relx=1, x=-SIDE_RAIL_WIDTH, y=0,
            width=SIDE_RAIL_WIDTH, relheight=1,
        )
        side.pack_propagate(False)
        tk.Label(
            side, text=f"{index + 1:02d}", bg="#111827", fg="#98a2b3",
            font=("Microsoft YaHei UI", TILE_NUMBER_FONT_SIZE, "bold")
        ).pack(fill="x", padx=3, pady=(5, 7), ipady=2)
        for label in ("主控", "开始", "停止", "主屏", "切换", "控制"):
            tk.Button(
                side, text=label, state="disabled",
                bg="#1d2939", fg="#667085", relief="flat",
                padx=1, pady=4, font=MAIN_BUTTON_FONT,
            ).pack(fill="x", padx=3, pady=2)

    def grid(self, row: int, column: int) -> None:
        self.frame.grid(
            row=row,
            column=column,
            padx=(0, WALL_GAP if column < WALL_COLUMNS - 1 else 0),
            pady=(0, WALL_GAP if row < WALL_ROWS - 1 else 0),
            sticky="nsew",
        )

    def destroy(self) -> None:
        self.frame.destroy()


class MasterView:
    def __init__(self, owner: "XinglanApp", parent: tk.Widget) -> None:
        self.owner = owner
        self.session: DeviceSession | None = None
        self.last_sequence = -1
        self.dragging = False
        self.last_touch_point: tuple[float, float] | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.render_size = (0, 0)
        self._status_presentation: tuple[str, str] | None = None
        width, height = MASTER_VIEW_SIZE
        self.image_bounds = (0, 0, width, height)

        self.frame = tk.Frame(
            parent,
            bg="#101828",
            highlightthickness=0,
        )
        # 标题和状态由右侧顶部工具栏、全局状态栏承载；画面本身占满可用区域。
        self.title = tk.Label(
            self.frame,
            text="主控大画面",
            bg="#050a11",
            fg="white",
            anchor="w",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.canvas = tk.Canvas(
            self.frame,
            width=width,
            height=height,
            bg="black",
            highlightthickness=0,
            cursor="arrow",
            takefocus=True,
        )
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.status = tk.Label(
            self.frame,
            text="请选择主控手机",
            bg="#101828",
            fg="#98a2b3",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<Control-v>", self._paste)
        self.canvas.bind("<Control-V>", self._paste)
        self.canvas.bind("<KeyPress>", self._key_press)
        self._show_placeholder("请选择主控手机")

    def set_session(self, session: DeviceSession | None) -> None:
        if self.session is session:
            return
        if self.dragging and self.session is not None and self.last_touch_point is not None:
            # Finish the old master's frozen route before changing canvases.
            self.owner.route_touch(
                self.session,
                0,
                *self.last_touch_point,
                from_master=True,
            )
        self.session = session
        self.last_sequence = -1
        self.dragging = False
        self.last_touch_point = None
        if session is None:
            self.title.configure(text="主控大画面")
            self._set_status("请选择主控手机", "#98a2b3")
            self._show_placeholder("请选择主控手机")
            return
        self.title.configure(text=f"主控大画面 · {self.owner.device_label(session.udid)}")
        self._set_status("正在连接", "#f79009")

    def _set_status(self, text: str, color: str) -> None:
        presentation = (text, color)
        if presentation == getattr(self, "_status_presentation", None):
            return
        self.status.configure(text=text, fg=color)
        self._status_presentation = presentation

    def refresh(self) -> None:
        if self.session is None:
            return
        sequence, _, image = self.session.latest.snapshot()
        stats = self.session.stats()
        if stats.status.startswith("投屏中"):
            text = f"重连{stats.reconnects}"
            color = "#12b76a"
        else:
            text = stats.status
            color = "#f79009"
        self._set_status(text, color)
        canvas_size = (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        if image is None or (sequence == self.last_sequence and canvas_size == self.render_size):
            return
        self.last_sequence = sequence
        self._show_image(image)

    def _show_placeholder(self, text: str) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width <= 1 or height <= 1:
            width, height = MASTER_VIEW_SIZE
        self.render_size = (width, height)
        self.image_bounds = (0, 0, width, height)
        image = Image.new("RGB", (width, height), "#050a11")
        draw = ImageDraw.Draw(image)
        draw.text(
            (width // 2, height // 2),
            text,
            fill="#98a2b3",
            anchor="mm",
            font=MASTER_PLACEHOLDER_FONT,
        )
        self._set_photo(image)

    def _set_photo(self, image: Image.Image) -> None:
        if (
            self.photo is not None
            and self.photo.width() == image.width
            and self.photo.height() == image.height
        ):
            self.photo.paste(image)
        else:
            self.photo = ImageTk.PhotoImage(image)
            self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _show_image(self, image: Image.Image) -> None:
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        self.render_size = (canvas_width, canvas_height)
        target_size = (canvas_width, canvas_height)
        resized = image if image.size == target_size else image.resize(
            target_size, Image.Resampling.LANCZOS
        )
        self.image_bounds = (0, 0, canvas_width, canvas_height)
        self._set_photo(resized)

    def _canvas_resized(self, _event: tk.Event) -> None:
        if self.session is None:
            self._show_placeholder("请选择主控手机")
        else:
            # 下一轮刷新按新画布尺寸重绘当前最新帧。
            self.last_sequence = -1

    def _normalized(self, event: tk.Event) -> tuple[float, float] | None:
        left, top, right, bottom = self.image_bounds
        if event.x < left or event.x >= right or event.y < top or event.y >= bottom:
            return None
        return (event.x - left) / (right - left), (event.y - top) / (bottom - top)

    def _press(self, event: tk.Event) -> None:
        if self.session is None:
            return
        point = self._normalized(event)
        if point is None:
            return
        self.canvas.focus_set()
        self.dragging = True
        self.last_touch_point = point
        self.owner.route_touch(self.session, 1, *point, from_master=True)

    def _key_press(self, event: tk.Event) -> str | None:
        if self.session is None:
            return None
        if event.state & 0x0004 and event.keysym.lower() == "v":
            return self._paste(event)
        return self.owner.route_keypress(
            self.session, event.keysym, event.char, from_master=True
        )

    def _paste(self, _event: tk.Event) -> str:
        if self.session is not None:
            self.owner.route_clipboard_paste(self.session, from_master=True)
        return "break"

    def _move(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        point = self._normalized(event)
        if point is not None:
            self.last_touch_point = point
            self.owner.route_touch(self.session, 2, *point, from_master=True)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        self.dragging = False
        point = self._normalized(event) or self.last_touch_point
        self.last_touch_point = None
        if point is not None:
            self.owner.route_touch(self.session, 0, *point, from_master=True)
        self.owner.activate_ime(
            self.session,
            from_master=True,
            screen_x=event.x_root,
            screen_y=event.y_root,
        )


class XinglanApp:
    def __init__(
        self,
        root: tk.Tk,
        max_devices: int = 10,
    ) -> None:
        self.root = root
        self.max_devices = max_devices
        self.max_groups = max(1, (max_devices + GROUP_SIZE - 1) // GROUP_SIZE)
        self.group_store = DeviceGroupStore(
            PROJECT_DIR / "config" / "device_groups.json",
            max_groups=self.max_groups,
            group_size=GROUP_SIZE,
        )
        self.sessions: dict[str, DeviceSession] = {}
        # Freeze recipients from DOWN through UP. Changing a checkbox, group,
        # or master mid-drag must not leave a phone with an unmatched contact.
        self._active_touch_routes: dict[
            tuple[str, bool], ActiveTouchRoute
        ] = {}
        # 只记录在线设备，不保持 6000/6203 连接。顶部开屏/熄屏点击时
        # 临时分批连接，命令结束后立即释放 USB 服务端口。
        self.action_hub = OnDemandDeviceActionHub()
        self.active_udids: set[str] = set()
        self.selected_udids: set[str] = set()
        self.tiles: dict[str, DeviceTile] = {}
        self.empty_slots: list[EmptySlot] = []
        self.wall_items: dict[int, DeviceTile | EmptySlot] = {}
        self._render_tile_cursor = 0
        self.missing_scans: dict[str, int] = {}
        self.master_udid: str | None = None
        self.sync_enabled = tk.BooleanVar(value=False)
        self.summary = tk.StringVar(value="准备扫描USB手机")
        self.health = tk.StringVar(value="帧率 0 · 内存 0 MB · 重连 0")
        self.device_counts = tk.StringVar(value="Windows检测中…")
        self.load_sampler = ProcessLoadSampler()
        self.health_tick = 0
        self._scan_in_progress = False
        self._usb_repair_in_progress = False
        self._usb_count_in_progress = False
        self._usb_count_after_id: str | None = None
        self._usb_topology_in_progress = False
        self._usb_topology_path = PROJECT_DIR / "config" / "usb_topology.json"
        self._usb_topology_snapshot: tuple[WindowsIphone, ...] = (
            load_usb_topology(self._usb_topology_path)
            if AUTO_USB_REPAIR_PROFILE
            else ()
        )
        self._auto_usb_repair_attempts = 0
        self._auto_usb_repair_last_attempt = 0.0
        self._usb_repair_is_automatic = False
        self.windows_device_count: int | None = None
        self.usbmux_device_count = 0
        self._closing = False
        self._retired_sessions: list[DeviceSession] = []
        self.music_player = WindowsMusicPlayer(MUSIC_TRACKS)
        self.music_track_index = 0
        self.music_playing = False
        self._music_after_id: str | None = None
        root.title("星澜")
        try:
            root.iconbitmap(default=str(PROJECT_DIR / "assets" / "xinglan.ico"))
        except tk.TclError:
            LOGGER.warning("failed to load legacy Xinglan window icon", exc_info=True)
        root.configure(bg="#0b1220")
        root.geometry("1280x900")
        try:
            root.state("zoomed")
        except tk.TclError:
            pass
        self.native_title_overlay = NativeTitleOverlay(
            root,
            "彭天霸",
            BRAND_BANNER_REL_X,
        )
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._ime_source: DeviceSession | None = None
        self._ime_from_master = False
        self.ime_worker = ImeWorkerClient(
            root,
            on_text=self._route_ime_text,
            on_key=self._route_ime_key,
        )

        # 严格复用旧版网页的 64px 顶栏和右侧 600px 操作区。
        toolbar = tk.Frame(root, bg="#1d2939", height=TOP_BAR_HEIGHT)
        toolbar.pack(fill="x", padx=10, pady=(2, 2))
        toolbar.pack_propagate(False)
        self.brand_banner_image = load_brand_banner(toolbar)
        tk.Label(
            toolbar,
            image=self.brand_banner_image,
            bg="#1d2939",
            borderwidth=0,
            highlightthickness=0,
        ).place(
            relx=BRAND_BANNER_REL_X,
            y=2,
            width=BRAND_BANNER_WIDTH,
            height=BRAND_BANNER_HEIGHT,
            anchor="n",
        )

        # 左侧箴言与右侧两行设备状态保持相同的宽度、字号和行距。
        motto_block = tk.Frame(toolbar, bg="#1d2939")
        motto_block.place(
            x=10,
            y=1,
            width=STATUS_BLOCK_WIDTH,
            height=38,
            anchor="nw",
        )
        tk.Label(
            motto_block, text=MOTTO_LINE_ONE, bg="#1d2939", fg="#d0d5dd",
            anchor="w", font=("Microsoft YaHei UI", 8),
        ).pack(fill="x")
        tk.Label(
            motto_block, text=MOTTO_LINE_TWO, bg="#1d2939", fg="#d0d5dd",
            anchor="w", font=("Microsoft YaHei UI", 8),
        ).pack(fill="x")

        # 四个顶部按钮与右侧主控栏共用同一条左右边界。
        # 这样“全部开屏”的左边缘会和下方“全选”严格对齐。
        top_actions = tk.Frame(toolbar, bg="#1d2939")
        top_actions.place(
            relx=1,
            x=0,
            y=3,
            width=RIGHT_PANEL_WIDTH,
            height=34,
            anchor="ne",
        )
        top_actions.grid_rowconfigure(0, weight=1)
        for column in range(4):
            top_actions.grid_columnconfigure(column, weight=1, uniform="top-actions")

        # 右侧只显示 Windows 与投屏通道数量，便于直接判断是否需要修复。
        status_block = tk.Frame(toolbar, bg="#1d2939")
        status_block.place(
            relx=1,
            # 与下方05号卡片整体最右边框对齐；7px是卡片墙与主控栏间距。
            x=-(RIGHT_PANEL_WIDTH + 7),
            y=5,
            width=STATUS_BLOCK_WIDTH,
            height=34,
            anchor="ne",
        )
        self.device_counts_label = tk.Label(
            status_block,
            textvariable=self.device_counts,
            bg="#1d2939",
            fg="#d0d5dd",
            anchor="se",
            justify="right",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.device_counts_label.pack(fill="both", expand=True)

        for column, (text, command) in enumerate(
            (
                ("修复端口", self.repair_usb_devices),
                ("全部开屏", self.wake_all_devices),
                ("全部熄屏", self.sleep_all_devices),
                ("分组设置", self.open_group_settings),
            )
        ):
            tk.Button(
                top_actions, text=text, command=command,
                bg="#2e90fa", fg="white", activebackground="#1570ef",
                activeforeground="white", relief="flat", borderwidth=0,
                font=MAIN_BUTTON_FONT,
            ).grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=1,
            )

        self.shell = tk.Frame(root, bg="#0b1220")
        self.shell.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        # 固定橙色外框恢复旧版尺寸。360px 总宽度扣除两侧各 2px
        # 边框后，内部正好保留 356px 给主控画面，不再由粗流光框挤压画面。
        self.right_panel = tk.Frame(
            self.shell,
            width=RIGHT_PANEL_WIDTH,
            bg="#101828",
            highlightthickness=MASTER_BORDER_THICKNESS,
            highlightbackground=MASTER_BORDER_COLOR,
            highlightcolor=MASTER_BORDER_COLOR,
        )
        self.right_panel.pack(side="right", fill="y", padx=(7, 0))
        self.right_panel.pack_propagate(False)
        self.right_panel.grid_columnconfigure(0, weight=1)
        # Keep the master canvas at the exact iPhone aspect-ratio height so the
        # image starts immediately below the toolbar instead of being centred
        # between two black letterbox bands.  All remaining vertical space is
        # deliberately handed to the action area below it.
        self.right_panel.grid_rowconfigure(1, weight=0)
        self.right_panel.grid_rowconfigure(2, weight=1)
        self.master_toolbar = tk.Frame(
            self.right_panel, height=34, bg="#050a11",
            highlightthickness=1, highlightbackground="#344054",
        )
        self.master_toolbar.grid(row=0, column=0, sticky="ew")
        tk.Button(
            self.master_toolbar, text="全选", command=self.select_all_devices,
            bg="#2e90fa", fg="white", relief="flat", padx=8,
            font=MAIN_BUTTON_FONT,
        ).pack(side="left", fill="y", padx=(5, 3), pady=3)
        self.sync_icons = {
            False: make_checkbox_icon(self.master_toolbar, 20, False),
            True: make_checkbox_icon(self.master_toolbar, 20, True),
        }
        self.sync_button = tk.Button(
            self.master_toolbar,
            text="同步群控",
            image=self.sync_icons[False],
            compound="left",
            command=self.toggle_sync_control,
            bg="#2e90fa",
            fg="white",
            activebackground="#1570ef",
            activeforeground="white",
            relief="flat",
            font=MAIN_BUTTON_FONT,
        )
        self.sync_button.pack(side="left", fill="both", expand=True, padx=2, pady=3)
        tk.Button(
            self.master_toolbar, text="全反", command=self.clear_device_selection,
            bg="#2e90fa", fg="white", relief="flat", padx=8,
            font=MAIN_BUTTON_FONT,
        ).pack(side="right", fill="y", padx=(3, 5), pady=3)
        self.master_view = MasterView(self, self.right_panel)
        self.master_view.frame.grid(row=1, column=0, sticky="nsew")
        self._build_right_controls()
        self.left_panel = tk.Frame(self.shell, bg="#0b1220")
        self.left_panel.pack(side="left", fill="both", expand=True)
        self.wall = tk.Frame(self.left_panel, bg="#0b1220")
        self.wall.pack(fill="both", expand=True)
        for column in range(WALL_COLUMNS):
            self.wall.grid_columnconfigure(column, weight=1, uniform="wall-columns")
        for row in range(WALL_ROWS):
            self.wall.grid_rowconfigure(row, weight=1, uniform="wall-rows")

        # 启动时只做一次同步扫描，后续扫描全部转到后台，绝不阻塞画面刷新。
        self._scan_devices_initial()
        if not self.tiles and not self.empty_slots:
            self._rebuild_tiles()
        root.after(DISPLAY_INTERVAL_MS, self.refresh_tiles)
        root.after(1000, self.refresh_health)
        root.after(2000, self.periodic_scan)
        root.after(200, self.refresh_windows_device_count)

    def _build_right_controls(self) -> None:
        """Build the right-side controls in the same order as the StarLan web UI."""
        self.controls = tk.Frame(self.right_panel, bg="#101828")
        self.controls.grid(row=2, column=0, sticky="nsew")
        self.controls.grid_columnconfigure(0, weight=1)
        for row_index in range(4):
            self.controls.grid_rowconfigure(
                row_index, weight=1, uniform="right-control-rows"
            )

        def fixed_row(parent: tk.Widget, row_index: int) -> tk.Frame:
            row = tk.Frame(parent, bg="#101828")
            row.grid(
                row=row_index,
                column=0,
                sticky="nsew",
                padx=2,
                pady=1,
            )
            return row

        group_row = tk.Frame(self.controls, bg="#101828")
        group_row.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 1))
        self.group_var = tk.StringVar(value="第1组")
        self.group_values: list[str] = []
        self.group_popup: tk.Toplevel | None = None
        self.group_buttons: dict[str, tk.Button] = {}
        def build_group_buttons(parent: tk.Widget, command) -> None:
            for index in range(1, self.max_groups + 1):
                value = f"第{index}组"
                button = tk.Button(
                    parent,
                    text=f"第\n{index}\n组",
                    command=lambda selected=value: command(selected),
                    anchor="center",
                    justify="center",
                    bg="#2e90fa",
                    fg="white",
                    activebackground="#2e90fa",
                    activeforeground="white",
                    relief="flat",
                    borderwidth=0,
                    highlightthickness=0,
                    padx=0,
                    pady=0,
                    takefocus=False,
                    font=MAIN_BUTTON_FONT,
                )
                # Every group receives the same expansion weight.  The deleted
                # connect button leaves no reserved or blank column.
                button.pack(
                    side="left",
                    fill="both",
                    expand=True,
                    padx=(0 if index == 1 else 1, 0 if index == self.max_groups else 1),
                )
                self.group_buttons[value] = button

        if GROUP_ONECLICK_PROFILE or GROUP_TWO_STEP_PROFILE:
            group_buttons_slot = tk.Frame(group_row, bg="#101828")
            group_buttons_slot.pack(fill="both", expand=True)
            group_command = (
                self.activate_group_two_step
                if GROUP_TWO_STEP_PROFILE
                else self.activate_group_button
            )
            build_group_buttons(group_buttons_slot, group_command)
        else:
            group_selector_slot = tk.Frame(
                group_row,
                bg="#101828",
                width=GROUP_SELECTOR_WIDTH,
            )
            group_selector_slot.pack(side="left", fill="y", padx=(0, 2))
            group_selector_slot.pack_propagate(False)

        if GROUP_BUTTONS_PROFILE and not (GROUP_ONECLICK_PROFILE or GROUP_TWO_STEP_PROFILE):
            # 原下拉框位置改成当前组的连接/断开按钮。
            self.group_button = tk.Button(
                group_selector_slot,
                text="连接本组",
                command=self.toggle_current_group,
                bg="#12b76a",
                fg="white",
                activebackground="#12b76a",
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                takefocus=False,
                font=MAIN_BUTTON_FONT,
            )
            self.group_button.pack(fill="both", expand=True)
            group_buttons_slot = tk.Frame(group_row, bg="#101828")
            group_buttons_slot.pack(side="left", fill="both", expand=True)
            build_group_buttons(group_buttons_slot, self._select_group)
        elif not (GROUP_ONECLICK_PROFILE or GROUP_TWO_STEP_PROFILE):
            self.group_combo = tk.Button(
                group_selector_slot,
                textvariable=self.group_var,
                command=self._toggle_group_popup,
                anchor="center",
                bg="#f2f4f7",
                fg="#101828",
                activebackground="#f2f4f7",
                activeforeground="#101828",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                padx=0,
                takefocus=False,
                font=MAIN_BUTTON_FONT,
            )
            self.group_combo.pack(fill="both", expand=True)
            self.group_button = tk.Button(
                group_row,
                text="连接本组",
                command=self.toggle_current_group,
                bg="#12b76a",
                fg="white",
                activebackground="#12b76a",
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                takefocus=False,
                font=MAIN_BUTTON_FONT,
            )
            self.group_button.pack(side="left", fill="both", expand=True)

        # 程序刚打开时就完整显示全部分组，不能等设备扫描或打开分组设置后
        # 才补齐菜单。默认 60 台对应第1组至第6组。
        self._refresh_group_selector()

        # 键盘输入由独立的轻量IME进程接收；它不加载投屏或分组代码。

        shortcut_row = fixed_row(self.controls, 1)
        for index, (text, command) in enumerate((
            ("切换", self.switch_window),
            ("主屏", lambda: self.route_system_action(SystemAction.HOME)),
            ("控制", lambda: self.route_system_action(SystemAction.CONTROL_CENTER)),
        )):
            tk.Button(
                shortcut_row, text=text, command=command,
                bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
                font=MAIN_BUTTON_FONT,
            ).pack(
                side="left",
                fill="both",
                expand=True,
                padx=(0 if index == 0 else 1, 0 if index == 2 else 1),
            )

        file_row = fixed_row(self.controls, 2)
        tk.Button(
            file_row, text="传到手机", command=self.open_file_transfer,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            font=MAIN_BUTTON_FONT,
        ).pack(side="left", fill="both", expand=True, padx=(0, 1))
        tk.Button(
            file_row, text="手机下载", command=self.open_phone_download,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            font=MAIN_BUTTON_FONT,
        ).pack(side="left", fill="both", expand=True, padx=(1, 0))

        mode_row = tk.Frame(self.controls, bg="#101828")
        mode_row.grid(row=3, column=0, sticky="nsew", padx=2, pady=(1, 2))
        tk.Button(
            mode_row, text="本组开屏", command=self.wake_current_group,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            takefocus=False,
            font=MAIN_BUTTON_FONT,
        ).pack(side="left", fill="both", expand=True, padx=(0, 1))
        tk.Button(
            mode_row, text="本组锁屏", command=self.lock_current_group,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            takefocus=False,
            font=MAIN_BUTTON_FONT,
        ).pack(side="left", fill="both", expand=True, padx=(1, 0))

    def _announce(self, text: str) -> None:
        self.summary.set(text)

    def _run_async_action(self, operation, callback) -> None:
        """Run a short USB control operation without blocking the Tk interface."""
        def worker() -> None:
            try:
                result = asyncio.run(operation())
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                result = exc
            try:
                self.root.after(0, lambda: callback(result))
            except tk.TclError:
                pass

        threading.Thread(
            target=worker,
            name="xinglan-device-action",
            daemon=True,
        ).start()

    def _all_online_udids(self) -> list[str]:
        return sorted(self.sessions)

    def _run_device_action(
        self,
        udids: list[str],
        action: str,
        label: str,
    ) -> None:
        udids = list(dict.fromkeys(udids))
        if not udids:
            self.summary.set(f"{label}：当前没有USB在线手机")
            return
        self.summary.set(f"{label}：正在向 {len(udids)} 台手机发送指令…")

        def completed(result) -> None:
            if isinstance(result, Exception):
                self.summary.set(f"{label}失败：{result}")
                return
            succeeded = sum(1 for ok in result.values() if ok)
            failed = len(result) - succeeded
            self.summary.set(f"{label}：成功 {succeeded} 台，失败 {failed} 台")

        # Open acknowledged XLStream control port 6203 only while this button
        # command is running. Port 6000 is an on-demand compatibility fallback
        # and is closed immediately after the write, so discovered phones no
        # longer hold permanent USB service connections.
        future = self.action_hub.broadcast_verified(udids, action)

        def finished(done_future) -> None:
            if done_future.cancelled():
                return
            try:
                result = done_future.result()
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                result = exc
            try:
                self.root.after(0, lambda value=result: completed(value))
            except tk.TclError:
                pass

        future.add_done_callback(finished)

    def _run_all_device_action(self, action: str, label: str) -> None:
        self._run_device_action(self._all_online_udids(), action, label)

    def wake_all_devices(self) -> None:
        self._run_all_device_action("wake", "全部开屏")

    def sleep_all_devices(self) -> None:
        self._run_all_device_action("sleep", "全部熄屏")

    def wake_current_group(self) -> None:
        group = self.group_var.get().strip() or "当前组"
        self._run_device_action(
            self._current_group_udids(),
            "wake",
            f"{group}开屏",
        )

    def lock_current_group(self) -> None:
        group = self.group_var.get().strip() or "当前组"
        self._run_device_action(
            self._current_group_udids(),
            "sleep",
            f"{group}锁屏",
        )

    def select_all_devices(self) -> None:
        targets = self._current_group_udids()
        self.selected_udids.update(targets)
        self._refresh_tile_selections()
        self.summary.set(f"已勾选当前组 {len(targets)} 台手机")

    def clear_device_selection(self) -> None:
        targets = set(self._current_group_udids())
        self.selected_udids.difference_update(targets)
        self._refresh_tile_selections()
        self.summary.set("已取消当前组全部手机的勾选")

    def toggle_sync_control(self) -> None:
        enabled = not self.sync_enabled.get()
        self.sync_enabled.set(enabled)
        self.sync_button.configure(image=self.sync_icons[enabled])
        self.summary.set(f"同步群控已{'开启' if enabled else '关闭'}")

    def is_device_selected(self, udid: str) -> bool:
        return udid in self.selected_udids

    def set_device_selected(self, udid: str, selected: bool) -> None:
        if selected:
            self.selected_udids.add(udid)
        else:
            self.selected_udids.discard(udid)
        selected_count = sum(
            udid in self.selected_udids for udid in self._current_group_udids()
        )
        self.summary.set(f"当前组已勾选 {selected_count} 台手机")

    def _refresh_tile_selections(self) -> None:
        for udid, tile in self.tiles.items():
            tile.set_selected(udid in self.selected_udids)

    def _selected_control_udids(self) -> list[str]:
        if self.sync_enabled.get():
            return [
                udid
                for udid in self._current_group_udids()
                if udid in self.active_udids and udid in self.selected_udids
            ]
        if self.master_udid and self.master_udid in self.active_udids:
            return [self.master_udid]
        return []

    def is_session_active(self, udid: str) -> bool:
        return udid in self.active_udids

    def device_label(self, udid: str) -> str:
        return self.group_store.label(udid)

    def _current_group_index(self) -> int:
        value = self.group_var.get().strip()
        try:
            return max(0, int(value.removeprefix("第").removesuffix("组")) - 1)
        except ValueError:
            return 0

    def _current_group_udids(self) -> list[str]:
        positioned = self.group_store.positioned_devices(
            self.sessions, self._current_group_index() + 1
        )
        return [udid for _, udid in positioned]

    def _refresh_group_selector(self) -> None:
        values = [f"第{index}组" for index in range(1, self.max_groups + 1)]
        current = min(self._current_group_index(), self.max_groups - 1)
        self._set_group_menu_values(values)
        self.group_var.set(values[current])
        self._refresh_group_selection_buttons()

    def _set_group_menu_values(self, values: list[str]) -> None:
        self.group_values = list(values)
        self._hide_group_popup()

    def _refresh_group_selection_buttons(self) -> None:
        selected = self.group_var.get()
        for value, button in getattr(self, "group_buttons", {}).items():
            is_selected = value == selected
            connected_count = 0
            target_count = 0
            if GROUP_ONECLICK_PROFILE or GROUP_TWO_STEP_PROFILE:
                try:
                    group_number = int(value.removeprefix("第").removesuffix("组"))
                except ValueError:
                    group_number = 0
                positioned = self.group_store.positioned_devices(self.sessions, group_number)
                target_udids = [udid for _, udid in positioned]
                target_count = len(target_udids)
                connected_count = sum(udid in self.active_udids for udid in target_udids)
            all_connected = target_count > 0 and connected_count == target_count
            partially_connected = GROUP_TWO_STEP_PROFILE and 0 < connected_count < target_count
            if all_connected or (GROUP_ONECLICK_PROFILE and connected_count > 0):
                background = "#12b76a"
            elif partially_connected:
                background = "#f79009"
            elif is_selected:
                background = "#f2f4f7"
            else:
                background = "#2e90fa"
            foreground = "#101828" if is_selected and connected_count == 0 else "white"
            button.configure(
                bg=background,
                fg=foreground,
                activebackground=background,
                activeforeground=foreground,
            )

    def _toggle_group_popup(self) -> None:
        popup = self.group_popup
        if popup is not None:
            try:
                if popup.winfo_exists() and popup.winfo_viewable():
                    self._hide_group_popup()
                    return
            except tk.TclError:
                pass
        self._show_group_popup()

    def _show_group_popup(self) -> None:
        if not self.group_values:
            return
        self._hide_group_popup()
        self.root.update_idletasks()
        width = max(1, self.group_combo.winfo_width())
        item_height = 27
        height = item_height * len(self.group_values) + 2
        x = self.group_combo.winfo_rootx()
        # 分组按钮保持原位，整个分组列表固定在按钮上方展开。
        y = max(0, self.group_combo.winfo_rooty() - height)

        popup = tk.Toplevel(self.root)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.root)
        popup.configure(bg="#98a2b3")
        popup.geometry(f"{width}x{height}+{x}+{y}")
        self.group_popup = popup

        for index, value in enumerate(self.group_values):
            button = tk.Button(
                popup,
                text=value,
                command=lambda selected=value: self._choose_group_from_popup(selected),
                anchor="center",
                bg="#f2f4f7",
                fg="#101828",
                activebackground="#2e90fa",
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                takefocus=False,
                font=MAIN_BUTTON_FONT,
            )
            button.place(
                x=1,
                y=1 + index * item_height,
                width=max(1, width - 2),
                height=item_height,
            )
        popup.bind("<Escape>", lambda _event: self._hide_group_popup())
        popup.bind(
            "<FocusOut>",
            lambda _event: self.root.after(50, self._hide_group_popup_if_unfocused),
        )
        popup.deiconify()
        popup.lift()
        popup.focus_force()

    def _hide_group_popup_if_unfocused(self) -> None:
        popup = self.group_popup
        if popup is None:
            return
        try:
            focused = popup.focus_get()
            if focused is not None and focused.winfo_toplevel() == popup:
                return
        except tk.TclError:
            pass
        self._hide_group_popup()

    def _hide_group_popup(self) -> None:
        popup = getattr(self, "group_popup", None)
        self.group_popup = None
        if popup is None:
            return
        try:
            popup.destroy()
        except tk.TclError:
            pass

    def _choose_group_from_popup(self, value: str) -> None:
        self._hide_group_popup()
        self._select_group(value)

    def toggle_music(self) -> None:
        if self.music_playing:
            self._stop_music(reset=True)
            self.summary.set("音乐已停止")
            return
        self.music_track_index = 0
        self._play_music_track()

    def _play_music_track(self) -> None:
        if self._music_after_id is not None:
            try:
                self.root.after_cancel(self._music_after_id)
            except tk.TclError:
                pass
            self._music_after_id = None
        try:
            self.music_player.play(self.music_track_index)
        except (MusicPlaybackError, OSError) as exc:
            self.music_playing = False
            self.music_button.configure(
                text="音乐", bg="#2e90fa", activebackground="#2e90fa"
            )
            self.summary.set(f"音乐播放失败：{exc}")
            LOGGER.warning("music playback failed", exc_info=True)
            return
        self.music_playing = True
        self.music_button.configure(
            text="停止", bg="#b42318", activebackground="#b42318"
        )
        self.summary.set(
            f"正在播放音乐 {self.music_track_index + 1}/{len(MUSIC_TRACKS)}"
        )
        self._music_after_id = self.root.after(500, self._poll_music_status)

    def _poll_music_status(self) -> None:
        self._music_after_id = None
        if not self.music_playing or self._closing:
            return
        try:
            mode = self.music_player.mode()
        except MusicPlaybackError as exc:
            self._stop_music(reset=True)
            self.summary.set(f"音乐播放失败：{exc}")
            return
        if mode == "stopped":
            self.music_track_index = (self.music_track_index + 1) % len(MUSIC_TRACKS)
            self._play_music_track()
            return
        self._music_after_id = self.root.after(500, self._poll_music_status)

    def _stop_music(self, reset: bool) -> None:
        self.music_playing = False
        if self._music_after_id is not None:
            try:
                self.root.after_cancel(self._music_after_id)
            except tk.TclError:
                pass
            self._music_after_id = None
        self.music_player.close()
        if reset:
            self.music_track_index = 0
        if hasattr(self, "music_button"):
            self.music_button.configure(
                text="音乐", bg="#2e90fa", activebackground="#2e90fa"
            )

    def _select_group(self, value: str) -> None:
        if self.group_var.get() == value:
            return
        self.group_var.set(value)
        self._refresh_group_selection_buttons()
        self._group_changed()

    def _group_changed(self, _event: tk.Event | None = None) -> None:
        self.ime_worker.deactivate()
        self._ime_source = None
        # A group switch is also an explicit projection boundary.  Keeping the
        # old group alive after its canvases disappear creates hidden video and
        # RFB sessions that continue consuming USB/CPU resources.
        stopped = self._stop_udids(list(self.active_udids))
        self.master_udid = None
        self._rebuild_tiles()
        self.summary.set(
            f"已切换到{self.group_var.get()}，已自动断开 {stopped} 台；"
            "点击连接本组后才开始投屏"
        )

    def _refresh_group_button(self) -> None:
        if GROUP_ONECLICK_PROFILE or GROUP_TWO_STEP_PROFILE:
            self._refresh_group_selection_buttons()
            return
        connected = any(udid in self.active_udids for udid in self._current_group_udids())
        color = "#912f20" if connected else "#12b76a"
        self.group_button.configure(
            text="断开本组" if connected else "连接本组",
            bg=color,
            activebackground=color,
        )

    def open_group_settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("固定分组设置")
        window.geometry("760x560")
        window.minsize(680, 480)
        window.configure(bg="#101828")
        window.transient(self.root)

        tk.Label(
            window,
            text="手机不会自动占位；先识别实体手机，再手动设置分组和编号。",
            bg="#101828",
            fg="white",
            anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill="x", padx=14, pady=(14, 8))

        columns = ("name", "udid", "group", "slot", "state")
        tree = ttk.Treeview(window, columns=columns, show="headings", height=15)
        headings = {
            "name": "名称",
            "udid": "手机ID后8位",
            "group": "分组",
            "slot": "编号",
            "state": "USB状态",
        }
        widths = {"name": 160, "udid": 150, "group": 80, "slot": 80, "state": 100}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="center")
        tree.pack(fill="both", expand=True, padx=14, pady=4)

        editor = tk.Frame(window, bg="#101828")
        editor.pack(fill="x", padx=14, pady=10)
        name_var = tk.StringVar()
        group_edit = tk.StringVar(value="1")
        slot_edit = tk.StringVar(value="1")
        tk.Label(editor, text="名称", bg="#101828", fg="white").grid(row=0, column=0, padx=4)
        ttk.Entry(editor, textvariable=name_var, width=20).grid(row=0, column=1, padx=4)
        tk.Label(editor, text="分组", bg="#101828", fg="white").grid(row=0, column=2, padx=4)
        ttk.Combobox(
            editor,
            textvariable=group_edit,
            values=[str(index) for index in range(1, self.max_groups + 1)],
            state="readonly",
            width=6,
        ).grid(row=0, column=3, padx=4)
        tk.Label(editor, text="组内编号", bg="#101828", fg="white").grid(row=0, column=4, padx=4)
        ttk.Combobox(
            editor,
            textvariable=slot_edit,
            values=[str(index) for index in range(1, GROUP_SIZE + 1)],
            state="readonly",
            width=6,
        ).grid(row=0, column=5, padx=4)
        editor.grid_columnconfigure(1, weight=1)

        def refresh_tree(selected_udid: str = "") -> None:
            for item in tree.get_children():
                tree.delete(item)
            all_udids = set(self.group_store.assignments) | set(self.sessions)
            ordered = sorted(
                all_udids,
                key=lambda udid: (
                    self.group_store.assignment(udid) is None,
                    self.group_store.assignment(udid).group
                    if self.group_store.assignment(udid) is not None else 999,
                    self.group_store.assignment(udid).slot
                    if self.group_store.assignment(udid) is not None else 999,
                    udid,
                ),
            )
            for udid in ordered:
                assignment = self.group_store.assignment(udid)
                tree.insert(
                    "",
                    "end",
                    iid=udid,
                    values=(
                        assignment.name if assignment and assignment.name else udid[-8:],
                        udid[-8:],
                        f"第{assignment.group}组" if assignment else "未设置",
                        f"{assignment.slot:02d}" if assignment else "--",
                        "USB在线" if udid in self.sessions else "离线",
                    ),
                )
            if selected_udid and tree.exists(selected_udid):
                tree.selection_set(selected_udid)
                tree.focus(selected_udid)
                tree.see(selected_udid)

        def load_selected(_event: tk.Event | None = None) -> None:
            selected = tree.selection()
            if not selected:
                return
            assignment = self.group_store.assignment(selected[0])
            if assignment is None:
                name_var.set("")
                group_edit.set("")
                slot_edit.set("")
                return
            name_var.set(assignment.name)
            group_edit.set(str(assignment.group))
            slot_edit.set(str(assignment.slot))

        def save_selected() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("固定分组", "请先在列表中选择一台手机。", parent=window)
                return
            udid = selected[0]
            try:
                if not group_edit.get() or not slot_edit.get():
                    raise ValueError("请手动选择分组和组内编号")
                group = int(group_edit.get())
                slot = int(slot_edit.get())
                self.group_store.assign(udid, group, slot, name_var.get())
            except ValueError as exc:
                messagebox.showerror("无法保存", str(exc), parent=window)
                return
            if udid in self.active_udids:
                self._stop_udids([udid])
            self.master_udid = None
            self._refresh_group_selector()
            self._rebuild_tiles()
            refresh_tree(udid)
            load_selected()
            self.summary.set(
                f"已固定 {self.device_label(udid)}：第{group}组 · 编号{slot:02d}"
            )

        def identify_selected() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("识别实体手机", "请先在列表中选择一台手机。", parent=window)
                return
            udid = selected[0]
            if udid not in self.sessions:
                messagebox.showinfo(
                    "识别实体手机",
                    "这台手机当前不在线，请先通过USB连接。",
                    parent=window,
                )
                return
            identify_button.configure(state="disabled", text="正在识别…")
            self.summary.set(f"正在识别实体手机：{udid[-8:]}")

            def completed(result) -> None:
                if not window.winfo_exists():
                    return
                identify_button.configure(state="normal", text="识别选中手机")
                if result is True:
                    self.summary.set(
                        f"识别完成：{self.device_label(udid)} 已完成一次熄屏再开屏"
                    )
                else:
                    detail = str(result) if isinstance(result, Exception) else "手机控制口没有响应"
                    self.summary.set(f"识别失败：{udid[-8:]}")
                    messagebox.showerror(
                        "识别失败",
                        f"没有收到这台手机的控制响应。\n{detail}",
                        parent=window,
                    )

            self._run_async_action(
                lambda: identify_physical_device(udid),
                completed,
            )

        tree.bind("<<TreeviewSelect>>", load_selected)
        buttons = tk.Frame(window, bg="#101828")
        buttons.pack(fill="x", padx=14, pady=(0, 14))
        tk.Button(
            buttons,
            text="保存当前手机",
            command=save_selected,
            bg="#12b76a",
            fg="white",
            relief="flat",
            padx=18,
            pady=7,
        ).pack(side="left")
        identify_button = tk.Button(
            buttons,
            text="识别选中手机",
            command=identify_selected,
            bg="#2e90fa",
            fg="white",
            relief="flat",
            padx=18,
            pady=7,
        )
        identify_button.pack(side="left", padx=8)
        tk.Button(
            buttons,
            text="关闭",
            command=window.destroy,
            bg="#344054",
            fg="white",
            relief="flat",
            padx=18,
            pady=7,
        ).pack(side="right")
        refresh_tree()
        if tree.get_children():
            first = tree.get_children()[0]
            tree.selection_set(first)
            load_selected()

    def _discard_touch_routes_for_udids(self, udids: set[str]) -> None:
        if not udids:
            return
        for route_key, route in list(self._active_touch_routes.items()):
            if route_key[0] in udids:
                # The source canvas is disappearing: close the complete
                # synchronized gesture while every target is still alive.
                for session in route.targets:
                    session.send_touch(0, route.x, route.y)
                self._active_touch_routes.pop(route_key, None)
                continue
            removed = tuple(
                session for session in route.targets if session.udid in udids
            )
            if not removed:
                continue
            for session in removed:
                session.send_touch(0, route.x, route.y)
            remaining = tuple(
                session for session in route.targets if session.udid not in udids
            )
            if remaining:
                route.targets = remaining
            else:
                self._active_touch_routes.pop(route_key, None)

    def _stop_udids(self, udids: list[str]) -> int:
        stopped = 0
        stopped_udids = {udid for udid in udids if udid in self.active_udids}
        # Route UP before stopping the corresponding RFB supervisors.
        self._discard_touch_routes_for_udids(stopped_udids)
        for udid in udids:
            if udid not in self.active_udids:
                continue
            old_session = self.sessions.get(udid)
            if old_session is not None:
                old_session.stop()
                self._retired_sessions.append(old_session)
                # 会话线程不可复用；保留USB设备卡片，但换成全新的待机会话。
                self.sessions[udid] = DeviceSession(udid)
            self.active_udids.discard(udid)
            stopped += 1
            LOGGER.info("manual projection stopped: %s", udid)
        if stopped:
            if self._ime_source is not None and self._ime_source.udid in stopped_udids:
                self.ime_worker.deactivate()
                self._ime_source = None
                self._ime_from_master = False
            # 给异步线程一点时间关闭USB通道，再主动回收PIL/Tk/PyAV对象。
            self.root.after(800, self._collect_released_resources)
        return stopped

    def _collect_released_resources(self) -> None:
        self._retired_sessions = [
            session
            for session in self._retired_sessions
            if not session.wait_stopped(timeout=0.0)
        ]
        if self._retired_sessions and not self._closing:
            self.root.after(400, self._collect_released_resources)
            return
        collected = gc.collect()
        LOGGER.info(
            "projection resources collected objects=%s active=%s memory=%.1fMB",
            collected,
            len(self.active_udids),
            working_set_mb(),
        )

    def start_device(self, udid: str) -> None:
        if udid not in self.sessions or udid in self.active_udids:
            return
        # 分组模式始终只让当前组占用视频和控制资源。
        current_group = set(self._current_group_udids())
        self._stop_udids([item for item in self.active_udids if item not in current_group])
        session = self.sessions[udid]
        session.start()
        self.active_udids.add(udid)
        self.master_udid = udid
        LOGGER.info("manual projection started: %s", udid)
        self._rebuild_tiles()
        self.summary.set(f"已手动开始手机 {udid[-8:]} 投屏")

    def stop_device(self, udid: str) -> None:
        stopped = self._stop_udids([udid])
        if stopped:
            if self.master_udid == udid:
                self.master_udid = None
            self._rebuild_tiles()
            self.summary.set(f"已停止手机 {udid[-8:]} 投屏并释放资源")

    def start_current_group(self) -> None:
        targets = self._current_group_udids()
        if not targets:
            self.summary.set("当前分组没有USB手机")
            return
        target_set = set(targets)
        self._stop_udids([udid for udid in self.active_udids if udid not in target_set])
        started = 0
        for udid in targets:
            if udid in self.active_udids:
                continue
            self.sessions[udid].start()
            self.active_udids.add(udid)
            LOGGER.info("manual projection started: %s", udid)
            started += 1
        self.master_udid = targets[0]
        self._rebuild_tiles()
        self.summary.set(
            f"{self.group_var.get()}已手动投屏 {len(targets)} 台，本次新连接 {started} 台"
        )
        self.refresh_windows_device_count(schedule_next=False)

    def stop_current_group(self) -> None:
        stopped = self._stop_udids(self._current_group_udids())
        self.master_udid = None
        self._rebuild_tiles()
        self.summary.set(
            f"{self.group_var.get()}已断开 {stopped} 台投屏，画面与USB通道已释放"
        )

    def toggle_current_group(self) -> None:
        if any(udid in self.active_udids for udid in self._current_group_udids()):
            self.stop_current_group()
        else:
            self.start_current_group()

    def activate_group_button(self, value: str) -> None:
        """Select/connect one group, or disconnect it when clicked again."""
        same_group = self.group_var.get() == value
        current_connected = same_group and any(
            udid in self.active_udids for udid in self._current_group_udids()
        )

        self.ime_worker.deactivate()
        self._ime_source = None
        # Always stop the complete active set first.  This is deliberately
        # stronger than stopping only the visible group: stale or forgotten
        # sessions can never survive as hidden projection work.
        stopped = self._stop_udids(list(self.active_udids))
        self.master_udid = None

        if not same_group:
            self.group_var.set(value)
        self._refresh_group_selection_buttons()

        if current_connected:
            self._rebuild_tiles()
            self.summary.set(f"{value}已断开 {stopped} 台投屏，未保留隐藏投屏")
            return

        targets = self._current_group_udids()
        if not targets:
            self._rebuild_tiles()
            self.summary.set(f"{value}没有USB手机；已断开原投屏，未保留隐藏投屏")
            return
        self.start_current_group()

    def activate_group_two_step(self, value: str) -> None:
        """First click selects a group; a repeat click connects or disconnects it."""
        same_group = self.group_var.get() == value
        if not same_group:
            self.ime_worker.deactivate()
            self._ime_source = None
            # Selecting another group is always a hard projection boundary.
            # Stop the complete active set so no invisible old group survives.
            stopped = self._stop_udids(list(self.active_udids))
            self.master_udid = None
            self.group_var.set(value)
            self._refresh_group_selection_buttons()
            self._rebuild_tiles()
            self.summary.set(
                f"已选择{value}，未开始整组投屏；已断开原投屏 {stopped} 台"
            )
            return

        targets = self._current_group_udids()
        target_set = set(targets)
        all_connected = bool(targets) and target_set.issubset(self.active_udids)
        if all_connected:
            self.ime_worker.deactivate()
            self._ime_source = None
            # Include any unexpected sessions outside the visible group too.
            stopped = self._stop_udids(list(self.active_udids))
            self.master_udid = None
            self._rebuild_tiles()
            self.summary.set(f"{value}已断开 {stopped} 台投屏，未保留隐藏投屏")
            return

        # Zero or partially connected: retain current individual sessions and
        # let start_current_group connect only the missing phones.
        self.start_current_group()

    def route_system_action(self, action: SystemAction) -> None:
        target_udids = self._selected_control_udids()
        if not target_udids:
            self.summary.set("请先选择主控手机，或开启同步后勾选手机")
            return
        targets = [self.sessions[udid] for udid in target_udids]
        accepted = sum(1 for session in targets if session.send_system_action(action))
        self.summary.set(f"已向 {accepted}/{len(targets)} 台发送系统操作")

    def route_single_system_action(
        self,
        session: DeviceSession,
        action: SystemAction,
    ) -> None:
        """Run a small-window shortcut on exactly one phone."""
        if session.udid not in self.active_udids:
            self.summary.set("这台手机尚未开始投屏")
            return
        labels = {
            SystemAction.HOME: "主屏幕",
            SystemAction.APP_SWITCHER: "App切换器",
            SystemAction.CONTROL_CENTER: "控制中心",
        }
        if session.send_system_action(action):
            self.summary.set(
                f"已向 {self.device_label(session.udid)} 发送{labels.get(action, '系统操作')}"
            )
        else:
            self.summary.set("单机快捷操作未发送：请确认该手机正在投屏")

    def open_single_control_center(self, session: DeviceSession) -> None:
        """Ask SpringBoard to open Control Center on exactly one phone."""
        self.route_single_system_action(session, SystemAction.CONTROL_CENTER)

    def switch_window(self) -> None:
        self.route_system_action(SystemAction.APP_SWITCHER)

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1024 * 1024 * 1024:
            return f"{size / (1024 ** 3):.2f} GB"
        if size >= 1024 * 1024:
            return f"{size / (1024 ** 2):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    def open_file_transfer(self) -> None:
        # 打开窗口时固定当前下拉框选中的一组。文件只发给这一组中
        # 已经连接投屏的手机，不再把相邻三组扩展为30台目标。
        group_number = self._current_group_index() + 1
        group_label = f"第{group_number}组"
        window = tk.Toplevel(self.root)
        window.title("传输电脑文件到手机")
        window.geometry("640x330")
        window.resizable(False, False)
        window.configure(bg="#101828")
        window.transient(self.root)
        # 将传输窗口放在投屏主窗口正中，而不是固定出现在屏幕左上角。
        # 先让 Tk 计算出窗口实际尺寸，再按主窗口的屏幕坐标定位。
        window.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        dialog_w = window.winfo_width()
        dialog_h = window.winfo_height()
        center_x = parent_x + max(0, (parent_w - dialog_w) // 2)
        center_y = parent_y + max(0, (parent_h - dialog_h) // 2)
        window.geometry(f"{dialog_w}x{dialog_h}+{center_x}+{center_y}")

        selected_path = tk.StringVar(value="")
        selected_text = tk.StringVar(value="尚未选择文件或文件夹")
        target_text = tk.StringVar()
        target_count_text = tk.StringVar()
        progress_text = tk.StringVar(value="请选择文件或文件夹后开始传输")
        import_photo = tk.BooleanVar(value=False)
        transferring = tk.BooleanVar(value=False)

        def current_targets() -> list[str]:
            # Restore the established control-target rule used by the old
            # transfer dialog: without sync, transfer only to the current
            # master; with sync, transfer only to checked active phones in
            # the current group.  "Select all" therefore means all 10 only
            # after synchronized control has been enabled.
            return self._selected_control_udids()

        def refresh_target_text() -> None:
            targets = current_targets()
            if targets:
                # 数量单独使用醒目的大号字体；目标较多时少展示一个名称，
                # 给右侧数量留出稳定空间，避免窗口宽度变化。
                labels = "、".join(self.device_label(udid) for udid in targets[:4])
                if len(targets) > 4:
                    labels += "…"
                target_text.set(f"{group_label}接收目标：{labels}")
                target_count_text.set(f"{len(targets)} 台")
            else:
                target_text.set(f"{group_label}接收目标：没有已连接投屏的手机")
                target_count_text.set("")

        def choose_file() -> None:
            value = filedialog.askopenfilename(
                parent=window,
                title="选择要传输到手机的文件",
            )
            if not value:
                return
            path = Path(value)
            selected_path.set(str(path))
            size = path.stat().st_size
            if size >= 1024 * 1024 * 1024:
                size_text = f"{size / (1024 ** 3):.2f} GB"
            elif size >= 1024 * 1024:
                size_text = f"{size / (1024 ** 2):.1f} MB"
            elif size >= 1024:
                size_text = f"{size / 1024:.1f} KB"
            else:
                size_text = f"{size} B"
            selected_text.set(f"已选择：{path.name} · {size_text}")
            media = path.suffix.lower() in {
                ".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp",
                ".mp4", ".mov", ".m4v",
            }
            if not media:
                import_photo.set(False)
            progress_text.set("文件已就绪，点击“开始传输”")
            photo_check.configure(state="normal")

        def choose_folder() -> None:
            value = filedialog.askdirectory(
                parent=window,
                title="选择要传输到手机的文件夹",
                mustexist=True,
            )
            if not value:
                return
            path = Path(value)
            selected_path.set(str(path))
            file_count = 0
            total_size = 0
            try:
                for item in path.rglob("*"):
                    if item.is_file() and not item.is_symlink():
                        file_count += 1
                        total_size += item.stat().st_size
            except OSError as exc:
                selected_path.set("")
                selected_text.set("尚未选择文件或文件夹")
                progress_text.set(f"无法读取文件夹：{exc}")
                return
            if total_size >= 1024 * 1024 * 1024:
                size_text = f"{total_size / (1024 ** 3):.2f} GB"
            elif total_size >= 1024 * 1024:
                size_text = f"{total_size / (1024 ** 2):.1f} MB"
            elif total_size >= 1024:
                size_text = f"{total_size / 1024:.1f} KB"
            else:
                size_text = f"{total_size} B"
            selected_text.set(
                f"已选择文件夹：{path.name} · {file_count} 个文件 · {size_text}"
            )
            import_photo.set(False)
            photo_check.configure(state="disabled")
            progress_text.set("文件夹已就绪，点击“开始传输”")

        def close_window() -> None:
            if transferring.get():
                return
            window.destroy()

        def start_transfer() -> None:
            path_text = selected_path.get()
            if not path_text:
                progress_text.set("请先选择文件或文件夹")
                return
            path = Path(path_text)
            if not path.exists() or (not path.is_file() and not path.is_dir()):
                progress_text.set("所选文件或文件夹已经不存在")
                return
            targets = current_targets()
            refresh_target_text()
            if not targets:
                progress_text.set(f"{group_label}没有已连接投屏的接收手机")
                return
            should_import_photo = import_photo.get()
            transferring.set(True)
            choose_button.configure(state="disabled")
            choose_folder_button.configure(state="disabled")
            start_button.configure(state="disabled", text="正在传输…")
            close_button.configure(state="disabled")
            progress_text.set(f"正在向{group_label} {len(targets)} 台手机传输，请稍候…")
            self.summary.set(
                f"正在向{group_label} {len(targets)} 台手机传输 {path.name}…"
            )

            def completed(result) -> None:
                transferring.set(False)
                if isinstance(result, Exception):
                    self.summary.set(f"文件传输失败：{result}")
                    if window.winfo_exists():
                        progress_text.set(f"传输失败：{result}")
                        choose_button.configure(state="normal")
                        choose_folder_button.configure(state="normal")
                        start_button.configure(state="normal", text="开始传输")
                        close_button.configure(state="normal")
                    return
                succeeded = sum(1 for item in result.values() if item.success)
                failed = len(result) - succeeded
                message = f"文件传输完成：成功 {succeeded} 台，失败 {failed} 台"
                self.summary.set(message)
                if not window.winfo_exists():
                    return
                progress_text.set(message)
                choose_button.configure(state="normal")
                choose_folder_button.configure(state="normal")
                start_button.configure(state="normal", text="开始传输")
                close_button.configure(state="normal")
                if failed:
                    details = [
                        f"{self.device_label(udid)}：{item.message or '传输失败'}"
                        for udid, item in result.items()
                        if not item.success
                    ]
                    messagebox.showwarning(
                        "部分手机传输失败",
                        "\n".join(details[:10]),
                        parent=window,
                    )

            self._run_async_action(
                lambda: send_file_to_devices(targets, path, should_import_photo),
                completed,
            )

        tk.Label(
            window,
            text="传输电脑文件到手机",
            bg="#101828",
            fg="white",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 12))
        file_row = tk.Frame(window, bg="#101828")
        file_row.pack(fill="x", padx=20)
        choose_button = tk.Button(
            file_row,
            text="选择文件",
            command=choose_file,
            bg="#2e90fa",
            fg="white",
            relief="flat",
            padx=16,
            pady=7,
        )
        choose_button.pack(side="left")
        choose_folder_button = tk.Button(
            file_row,
            text="选择文件夹",
            command=choose_folder,
            bg="#2e90fa",
            fg="white",
            relief="flat",
            padx=16,
            pady=7,
        )
        choose_folder_button.pack(side="left", padx=(8, 0))
        tk.Label(
            file_row,
            textvariable=selected_text,
            bg="#101828",
            fg="#d0d5dd",
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=12)
        photo_check = tk.Checkbutton(
            window,
            text="图片或视频传完后导入系统“照片”",
            variable=import_photo,
            bg="#101828",
            fg="white",
            activebackground="#101828",
            activeforeground="white",
            selectcolor="#2e90fa",
        )
        photo_check.pack(anchor="w", padx=20, pady=(18, 8))
        target_row = tk.Frame(window, bg="#101828")
        target_row.pack(fill="x", padx=20, pady=4)
        tk.Label(
            target_row,
            textvariable=target_count_text,
            bg="#101828",
            fg="#fdb022",
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="e",
        # 与下方右侧“开始传输”按钮的中心位置对齐。
        ).pack(side="right", padx=(12, 30))
        tk.Label(
            target_row,
            textvariable=target_text,
            bg="#101828",
            fg="#d0d5dd",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            window,
            textvariable=progress_text,
            bg="#101828",
            fg="#84caff",
            anchor="w",
        ).pack(fill="x", padx=20, pady=8)
        actions = tk.Frame(window, bg="#101828")
        actions.pack(side="bottom", fill="x", padx=20, pady=18)
        close_button = tk.Button(
            actions,
            text="关闭",
            command=close_window,
            bg="#344054",
            fg="white",
            relief="flat",
            padx=22,
            pady=8,
        )
        close_button.pack(side="left")
        start_button = tk.Button(
            actions,
            text="开始传输",
            command=start_transfer,
            bg="#12b76a",
            fg="white",
            relief="flat",
            padx=28,
            pady=8,
        )
        start_button.pack(side="right")
        window.protocol("WM_DELETE_WINDOW", close_window)
        refresh_target_text()

    def open_phone_download(self) -> None:
        """手机→电脑：浏览手机文件系统，选择文件夹下载到桌面。"""
        group_number = self._current_group_index() + 1
        window = tk.Toplevel(self.root)
        window.title("手机下载到电脑")
        window.geometry("640x620")
        window.resizable(False, False)
        window.configure(bg="#101828")
        window.transient(self.root)
        window.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        dialog_w = window.winfo_width()
        dialog_h = window.winfo_height()
        center_x = parent_x + max(0, (parent_w - dialog_w) // 2)
        center_y = parent_y + max(0, (parent_h - dialog_h) // 2)
        window.geometry(f"{dialog_w}x{dialog_h}+{center_x}+{center_y}")

        # 默认路径：我的iPhone = /var/mobile/Documents
        DEFAULT_PATH = "/var/mobile/Documents"
        current_path = tk.StringVar(value=DEFAULT_PATH)
        display_path = tk.StringVar(value="文件 > 我的iPhone")
        selected_folder = tk.StringVar(value="")
        selected_meta = tk.StringVar(value="尚未选择文件夹")
        save_dir = tk.StringVar(value=str(Path.home() / "Desktop"))
        progress_text = tk.StringVar(value="请选择手机后点击刷新，浏览文件列表")
        downloading = tk.BooleanVar(value=False)
        list_entries: list[dict] = []

        # 获取当前组已连接投屏的手机
        def connected_devices() -> list[tuple[str, str]]:
            result = []
            for udid in self._current_group_udids():
                if udid in self.active_udids:
                    result.append((udid, self.device_label(udid)))
            return result

        devices = connected_devices()
        device_var = tk.StringVar()
        if devices:
            device_var.set(devices[0][1])

        def current_udid() -> str | None:
            for udid, label in devices:
                if label == device_var.get():
                    return udid
            return None

        def refresh_list() -> None:
            udid = current_udid()
            if not udid:
                progress_text.set("没有已连接投屏的手机")
                return
            path = current_path.get()
            progress_text.set(f"正在读取 {path} …")
            for item in file_list.get_children():
                file_list.delete(item)
            list_entries.clear()

            def callback(result) -> None:
                if isinstance(result, Exception):
                    progress_text.set(f"读取失败：{result}")
                    return
                if not result.success:
                    progress_text.set(f"读取失败：{result.message}")
                    return
                for entry in result.entries:
                    icon = "📁" if entry.directory else "📄"
                    size_text = "—" if entry.directory else _format_size(entry.size)
                    file_list.insert(
                        "", "end",
                        values=(f"{icon} {entry.name}", size_text),
                        tags=("dir" if entry.directory else "file",),
                    )
                    list_entries.append({"name": entry.name, "directory": entry.directory, "size": entry.size})
                progress_text.set(f"当前路径：{display_path.get()}（{len(result.entries)} 项）")

            self._run_async_action(lambda: list_directory(udid, path), callback)

        def on_double_click(_event) -> None:
            selection = file_list.selection()
            if not selection:
                return
            index = file_list.index(selection[0])
            if index >= len(list_entries):
                return
            entry = list_entries[index]
            if not entry["directory"]:
                return
            new_path = current_path.get().rstrip("/") + "/" + entry["name"]
            current_path.set(new_path)
            display_path.set(f"文件 > 我的iPhone > {entry['name']}")
            selected_folder.set("")
            selected_meta.set("尚未选择文件夹")
            refresh_list()

        def on_select(_event) -> None:
            selection = file_list.selection()
            if not selection:
                return
            index = file_list.index(selection[0])
            if index >= len(list_entries):
                return
            entry = list_entries[index]
            if entry["directory"]:
                full_path = current_path.get().rstrip("/") + "/" + entry["name"]
                selected_folder.set(full_path)
                selected_meta.set(f"已选文件夹：{entry['name']}")
            else:
                selected_folder.set("")
                selected_meta.set("请选择文件夹（文件不可单独下载）")

        def go_parent() -> None:
            path = current_path.get()
            if path == DEFAULT_PATH or path == "/var/mobile" or path == "/":
                return
            parent = str(Path(path).parent)
            current_path.set(parent)
            if parent == DEFAULT_PATH:
                display_path.set("文件 > 我的iPhone")
            else:
                display_path.set(f"文件 > 我的iPhone > {Path(parent).name}")
            selected_folder.set("")
            selected_meta.set("尚未选择文件夹")
            refresh_list()

        def go_path() -> None:
            value = path_entry.get().strip()
            if not value:
                return
            current_path.set(value)
            display_path.set(value)
            selected_folder.set("")
            selected_meta.set("尚未选择文件夹")
            refresh_list()

        def choose_save_dir() -> None:
            value = filedialog.askdirectory(parent=window, title="选择保存位置", initialdir=save_dir.get())
            if value:
                save_dir.set(value)

        def start_download() -> None:
            udid = current_udid()
            if not udid:
                progress_text.set("没有已连接投屏的手机")
                return
            folder = selected_folder.get()
            if not folder:
                progress_text.set("请先在列表中选择一个文件夹")
                return
            downloading.set(True)
            download_btn.configure(state="disabled", text="正在下载…")
            refresh_btn.configure(state="disabled")
            parent_btn.configure(state="disabled")
            progress_text.set(f"正在下载 {Path(folder).name} 到桌面…")
            self.summary.set(f"正在从手机下载 {Path(folder).name} …")

            def callback(result) -> None:
                downloading.set(False)
                download_btn.configure(state="normal", text="开始下载")
                refresh_btn.configure(state="normal")
                parent_btn.configure(state="normal")
                if isinstance(result, Exception):
                    msg = f"下载失败：{result}"
                    progress_text.set(msg)
                    self.summary.set(msg)
                    messagebox.showerror("下载失败", str(result), parent=window)
                    return
                if not result.success:
                    msg = f"下载失败：{result.message}"
                    progress_text.set(msg)
                    self.summary.set(msg)
                    messagebox.showwarning("下载失败", result.message, parent=window)
                    return
                msg = f"下载完成：{result.total_files} 个文件，已保存到 {result.saved_path}"
                progress_text.set(msg)
                self.summary.set(f"手机下载完成：{Path(folder).name}（{result.total_files} 个文件）")
                messagebox.showinfo("下载完成", msg, parent=window)

            self._run_async_action(
                lambda: download_folder(udid, folder, Path(save_dir.get())),
                callback,
            )

        def close_window() -> None:
            if downloading.get():
                return
            window.destroy()

        # UI 构建
        tk.Label(
            window, text="从手机下载文件夹到电脑",
            bg="#101828", fg="white", font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w", padx=20, pady=(16, 10))

        # 设备选择
        device_row = tk.Frame(window, bg="#101828")
        device_row.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(device_row, text="手机：", bg="#101828", fg="#94a3b8", font=("Microsoft YaHei UI", 11)).pack(side="left")
        device_menu = ttk.Combobox(
            device_row, textvariable=device_var,
            values=[label for _, label in devices],
            state="readonly", width=30,
        )
        device_menu.pack(side="left", padx=(6, 0))

        # 路径导航
        nav_row = tk.Frame(window, bg="#101828")
        nav_row.pack(fill="x", padx=20, pady=(0, 6))
        parent_btn = tk.Button(nav_row, text="← 上一级", command=go_parent, bg="#1e293b", fg="#94a3b8", relief="flat", padx=10, pady=4, font=("Microsoft YaHei UI", 10))
        parent_btn.pack(side="left", padx=(0, 6))
        path_entry = tk.Entry(nav_row, textvariable=display_path, bg="#0c1728", fg="#edf7ff", relief="flat", insertbackground="#edf7ff", font=("Microsoft YaHei UI", 10))
        path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(nav_row, text="跳转", command=go_path, bg="#1e293b", fg="#94a3b8", relief="flat", padx=10, pady=4, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 6))
        refresh_btn = tk.Button(nav_row, text="刷新", command=refresh_list, bg="#1e293b", fg="#94a3b8", relief="flat", padx=10, pady=4, font=("Microsoft YaHei UI", 10))
        refresh_btn.pack(side="left")

        # 文件列表
        list_frame = tk.Frame(window, bg="#0c1728", highlightbackground="#223a57", highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        file_list = ttk.Treeview(
            list_frame, columns=("name", "size"),
            show="headings", height=12,
        )
        file_list.heading("name", text="名称")
        file_list.heading("size", text="大小")
        file_list.column("name", width=440, anchor="w")
        file_list.column("size", width=100, anchor="e")
        file_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=file_list.yview)
        scrollbar.pack(side="right", fill="y")
        file_list.configure(yscrollcommand=scrollbar.set)
        file_list.bind("<Double-1>", on_double_click)
        file_list.bind("<<TreeviewSelect>>", on_select)
        file_list.tag_configure("dir", foreground="#7dd3fc")
        file_list.tag_configure("file", foreground="#cbd5e1")

        # 选中状态
        selected_row = tk.Frame(window, bg="#0c1728", highlightbackground="#223a57", highlightthickness=1)
        selected_row.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(selected_row, textvariable=selected_meta, bg="#0c1728", fg="#7dd3fc", font=("Microsoft YaHei UI", 10), anchor="w").pack(fill="x", padx=10, pady=5)

        # 保存位置
        save_row = tk.Frame(window, bg="#101828")
        save_row.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(save_row, text="保存到：", bg="#101828", fg="#94a3b8", font=("Microsoft YaHei UI", 11)).pack(side="left")
        tk.Label(save_row, textvariable=save_dir, bg="#0c1728", fg="#edf7ff", font=("Microsoft YaHei UI", 10), anchor="w", padx=8, pady=4).pack(side="left", fill="x", expand=True, padx=(6, 6))
        tk.Button(save_row, text="更改", command=choose_save_dir, bg="#1e293b", fg="#94a3b8", relief="flat", padx=12, pady=4, font=("Microsoft YaHei UI", 10)).pack(side="left")

        # 进度和按钮
        tk.Label(window, textvariable=progress_text, bg="#101828", fg="#84caff", anchor="w", font=("Microsoft YaHei UI", 10)).pack(fill="x", padx=20, pady=(0, 6))
        actions = tk.Frame(window, bg="#101828")
        actions.pack(side="bottom", fill="x", padx=20, pady=(0, 16))
        tk.Button(actions, text="关闭", command=close_window, bg="#344054", fg="white", relief="flat", padx=22, pady=8, font=("Microsoft YaHei UI", 11)).pack(side="left")
        download_btn = tk.Button(actions, text="开始下载", command=start_download, bg="#12b76a", fg="white", relief="flat", padx=28, pady=8, font=("Microsoft YaHei UI", 11, "bold"))
        download_btn.pack(side="right")

        window.protocol("WM_DELETE_WINDOW", close_window)
        if devices:
            refresh_list()
        else:
            progress_text.set("当前组没有已连接投屏的手机")

    def _discover_devices(self) -> list[str]:
        return discover_usb_udids_stable(PROJECT_DIR)[: self.max_devices]

    def _scan_devices_initial(self) -> None:
        try:
            udids = self._discover_devices()
        except Exception as exc:
            self.summary.set(f"USB扫描失败：{exc}")
            LOGGER.warning("USB initial scan failed: %s", exc)
            return
        self._apply_device_scan(udids)

    def scan_devices(self) -> None:
        """在后台扫描USB设备，避免60台枚举冻结Tk画面刷新。"""
        if self._closing or self._scan_in_progress or self._usb_repair_in_progress:
            return
        self._scan_in_progress = True

        def worker() -> None:
            try:
                result: tuple[list[str] | None, str | None] = (
                    self._discover_devices(),
                    None,
                )
            except Exception as exc:
                result = (None, str(exc))
            try:
                self.root.after(0, lambda value=result: self._finish_device_scan(value))
            except tk.TclError:
                pass

        threading.Thread(
            target=worker,
            name="xinglan-usb-scan",
            daemon=True,
        ).start()

    def repair_usb_devices(self, *, automatic: bool = False) -> bool:
        """Recover only affected external hub branches; never cycle a root hub."""
        if self._closing or self._usb_repair_in_progress:
            return False
        if automatic:
            try:
                elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except (AttributeError, OSError):
                elevated = False
            if not elevated:
                self.summary.set("检测到USB掉线：自动修复需要以管理员身份启动软件")
                LOGGER.warning("automatic USB repair skipped: process is not elevated")
                return False
        self._usb_repair_in_progress = True
        self._usb_repair_is_automatic = automatic
        self.summary.set(
            "检测到USB掉线，正在自动修复…"
            if automatic
            else "正在检查Windows与投屏USB通道…"
        )

        def set_progress(text: str) -> None:
            try:
                self.root.after(0, lambda value=text: self.summary.set(value))
            except tk.TclError:
                pass

        def worker() -> None:
            try:
                result: tuple[UsbRepairResult | None, str | None] = (
                    repair_usb(
                        PROJECT_DIR,
                        progress=set_progress,
                        known_windows_iphones=(
                            self._usb_topology_snapshot
                            if AUTO_USB_REPAIR_PROFILE
                            else ()
                        ),
                    ),
                    None,
                )
            except Exception as exc:
                LOGGER.exception("USB branch repair failed")
                result = (None, str(exc))
            try:
                self.root.after(
                    0,
                    lambda value=result, is_auto=automatic:
                        self._finish_usb_repair(value, automatic=is_auto),
                )
            except tk.TclError:
                pass

        threading.Thread(
            target=worker,
            name="xinglan-usb-repair",
            daemon=True,
        ).start()
        return True

    def _finish_usb_repair(
        self,
        result: tuple[UsbRepairResult | None, str | None],
        *,
        automatic: bool = False,
    ) -> None:
        self._usb_repair_in_progress = False
        self._usb_repair_is_automatic = False
        if self._closing:
            return
        repair_result, error = result
        if error is not None or repair_result is None:
            prefix = "USB自动修复失败" if automatic else "USB修复失败"
            self.summary.set(f"{prefix}：{error or '未知错误'}")
            return
        if repair_result.cycled_branches == 0:
            self.summary.set(
                f"USB通道正常：Windows与投屏均识别 {repair_result.after_count} 台"
            )
        else:
            self.summary.set(
                f"USB修复完成：{repair_result.before_count}→{repair_result.after_count}/"
                f"{repair_result.windows_count} 台，闪断 {repair_result.cycled_branches} 个分支"
            )
        self.windows_device_count = repair_result.windows_count
        self.usbmux_device_count = repair_result.after_count
        self._update_device_counts_text()
        self.scan_devices()
        expected_count = len(self._usb_topology_snapshot)
        if (
            AUTO_USB_REPAIR_PROFILE
            and
            repair_result.after_count >= expected_count
            and repair_result.windows_count >= expected_count
        ):
            self._auto_usb_repair_attempts = 0
            self._capture_usb_topology(force=True)

    def _finish_device_scan(
        self,
        result: tuple[list[str] | None, str | None],
    ) -> None:
        self._scan_in_progress = False
        if self._closing:
            return
        udids, error = result
        if error is not None or udids is None:
            self.summary.set(f"USB扫描失败：{error or '未知错误'}")
            LOGGER.warning("USB scan failed: %s", error or "unknown error")
            return
        self._apply_device_scan(udids)

    def _apply_device_scan(self, udids: list[str]) -> None:
        current = set(udids)
        self.usbmux_device_count = len(current)
        self._update_device_counts_text()
        # 新手机只进入“未设置”列表，绝不自动占用分组位置。
        changed = False
        for udid in list(self.sessions):
            if udid in current:
                self.missing_scans[udid] = 0
                continue
            misses = self.missing_scans.get(udid, 0) + 1
            self.missing_scans[udid] = misses
            # 连续3轮（约6秒）都不存在才认定拔线，过滤Apple USB瞬时扫描抖动。
            if misses >= 3:
                self._discard_touch_routes_for_udids({udid})
                self.sessions.pop(udid).stop()
                self.active_udids.discard(udid)
                self.missing_scans.pop(udid, None)
                changed = True

        for udid in udids:
            if udid in self.sessions:
                continue
            session = DeviceSession(udid)
            self.sessions[udid] = session
            # 与旧版一致：新出现在当前界面的手机默认处于勾选状态。
            self.selected_udids.add(udid)
            self.missing_scans[udid] = 0
            LOGGER.info("USB device discovered, waiting for manual projection: %s", udid)
            changed = True

        stable_udids = sorted(self.sessions)
        self.action_hub.update_devices(stable_udids)

        if changed:
            self._refresh_group_selector()
            self._rebuild_tiles()
        retained = len(stable_udids) - len(udids)
        if retained > 0:
            missing_suffixes = sorted(udid[-8:] for udid in set(stable_udids) - current)
            LOGGER.warning(
                "transient USB scan omission raw=%s stable=%s retained=%s devices=%s",
                len(udids), len(stable_udids), retained, ",".join(missing_suffixes),
            )
        self.summary.set(
            f"发现 {len(stable_udids)} 台USB手机 · 默认不自动投屏"
        )
        self._update_device_counts_text()

    def _update_device_counts_text(self) -> None:
        windows_text = "检测中" if self.windows_device_count is None else str(self.windows_device_count)
        expected_count = max(
            len(self._usb_topology_snapshot),
            self.windows_device_count or 0,
        )
        missing = max(
            0,
            expected_count - (self.windows_device_count or 0),
            expected_count - self.usbmux_device_count,
        )
        self.device_counts.set(
            f"Windows {windows_text}台 · 投屏识别 {self.usbmux_device_count}台"
            f" · 掉线 {missing}台"
        )
        color = "#f97066" if missing > 0 else "#d0d5dd"
        self.device_counts_label.configure(fg=color)

    def _capture_usb_topology(self, *, force: bool = False) -> None:
        """Capture the healthy phone-to-hub map only when a full scan is needed."""
        if (
            self._closing
            or self._usb_topology_in_progress
            or self._usb_repair_in_progress
            or (self._usb_topology_snapshot and not force)
        ):
            return
        self._usb_topology_in_progress = True

        def worker() -> None:
            try:
                result: tuple[list[WindowsIphone] | None, str | None] = (
                    discover_windows_iphones(),
                    None,
                )
            except Exception as exc:
                result = (None, str(exc))
            try:
                self.root.after(
                    0,
                    lambda value=result: self._finish_usb_topology_capture(value),
                )
            except tk.TclError:
                pass

        threading.Thread(
            target=worker,
            name="xinglan-usb-topology",
            daemon=True,
        ).start()

    def _finish_usb_topology_capture(
        self,
        result: tuple[list[WindowsIphone] | None, str | None],
    ) -> None:
        self._usb_topology_in_progress = False
        if self._closing:
            return
        phones, error = result
        if error is not None or not phones:
            LOGGER.warning("USB topology capture failed: %s", error or "empty result")
            return
        if self.windows_device_count is not None and len(phones) < self.windows_device_count:
            LOGGER.warning(
                "USB topology capture ignored: records=%s Windows=%s",
                len(phones), self.windows_device_count,
            )
            return
        if self._usb_topology_snapshot and len(phones) < len(self._usb_topology_snapshot):
            LOGGER.warning(
                "USB topology capture kept healthy snapshot: records=%s snapshot=%s",
                len(phones), len(self._usb_topology_snapshot),
            )
            return
        self._usb_topology_snapshot = tuple(phones)
        try:
            save_usb_topology(self._usb_topology_path, phones)
        except OSError:
            LOGGER.exception("failed to persist USB topology snapshot")
        LOGGER.info("USB topology snapshot updated: devices=%s", len(phones))
        if self.windows_device_count is not None:
            self._consider_auto_usb_repair(self.windows_device_count)

    def _consider_auto_usb_repair(self, windows_count: int) -> None:
        """Start bounded automatic repair only after the 30-second count changes."""
        baseline = len(self._usb_topology_snapshot)
        if baseline == 0:
            if windows_count > 0:
                self._capture_usb_topology()
            return

        if windows_count >= baseline and self.usbmux_device_count >= baseline:
            self._auto_usb_repair_attempts = 0
            if windows_count > baseline:
                self._capture_usb_topology(force=True)
            return

        if windows_count == 0 and self.usbmux_device_count == 0:
            self.summary.set("检测到全部USB手机离线，已停止自动闪断以保护全部端口")
            return
        if self._usb_repair_in_progress:
            return
        if self._auto_usb_repair_attempts >= AUTO_USB_REPAIR_MAX_ATTEMPTS:
            self.summary.set("USB自动修复已尝试2次，请检查充电桩、数据线或供电")
            return
        now = time.monotonic()
        if now - self._auto_usb_repair_last_attempt < AUTO_USB_REPAIR_COOLDOWN_SECONDS:
            return
        if self.repair_usb_devices(automatic=True):
            self._auto_usb_repair_attempts += 1
            self._auto_usb_repair_last_attempt = now

    def refresh_windows_device_count(self, *, schedule_next: bool = True) -> None:
        """Refresh Windows PnP count without blocking rendering or overlapping runs."""
        if self._closing:
            return
        if schedule_next and self._usb_count_after_id is not None:
            try:
                self.root.after_cancel(self._usb_count_after_id)
            except tk.TclError:
                pass
            self._usb_count_after_id = None
        if self._usb_repair_in_progress:
            if schedule_next:
                self._usb_count_after_id = self.root.after(
                    WINDOWS_USB_REFRESH_MS,
                    self.refresh_windows_device_count,
                )
            return
        if self._usb_count_in_progress:
            if schedule_next:
                self._usb_count_after_id = self.root.after(
                    WINDOWS_USB_REFRESH_MS,
                    self.refresh_windows_device_count,
                )
            return
        self._usb_count_in_progress = True

        def worker() -> None:
            try:
                result: tuple[int | None, str | None] = (
                    discover_windows_iphone_count(),
                    None,
                )
            except Exception as exc:
                result = (None, str(exc))
            try:
                self.root.after(
                    0,
                    lambda value=result, repeat=schedule_next:
                        self._finish_windows_device_count(value, repeat),
                )
            except tk.TclError:
                pass

        threading.Thread(
            target=worker,
            name="xinglan-windows-usb-count",
            daemon=True,
        ).start()

    def _finish_windows_device_count(
        self,
        result: tuple[int | None, str | None],
        schedule_next: bool,
    ) -> None:
        self._usb_count_in_progress = False
        if self._closing:
            return
        count, error = result
        if error is None and count is not None:
            self.windows_device_count = count
            self._update_device_counts_text()
            if AUTO_USB_REPAIR_PROFILE:
                self._consider_auto_usb_repair(count)
        else:
            LOGGER.warning("Windows USB count failed: %s", error or "unknown error")
        if schedule_next:
            self._usb_count_after_id = self.root.after(
                WINDOWS_USB_REFRESH_MS,
                self.refresh_windows_device_count,
            )

    def _rebuild_tiles(self) -> None:
        if FLICKER_FREE_WALL_PROFILE:
            self._rebuild_tiles_without_flash()
            return
        self._rebuild_tiles_legacy()

    def _rebuild_tiles_legacy(self) -> None:
        ordered = self._current_group_udids()
        for tile in self.tiles.values():
            tile.destroy()
        self.tiles.clear()
        for slot in self.empty_slots:
            slot.destroy()
        self.empty_slots.clear()
        tile_width, tile_height = TILE_VIEW_SIZE
        group_number = self._current_group_index() + 1
        connected_by_slot = dict(
            self.group_store.positioned_devices(self.sessions, group_number)
        )
        for index in range(GROUP_SIZE):
            slot_number = index + 1
            udid = connected_by_slot.get(slot_number)
            if udid is not None:
                tile = DeviceTile(
                    self,
                    self.wall,
                    self.sessions[udid],
                    index,
                    tile_width,
                    tile_height,
                )
                tile.grid(index // WALL_COLUMNS, index % WALL_COLUMNS)
                self.tiles[udid] = tile
                continue
            assigned_udid = self.group_store.udid_at(group_number, slot_number)
            assigned_label = (
                self.group_store.label(assigned_udid) if assigned_udid else ""
            )
            slot = EmptySlot(self.wall, index, assigned_label)
            slot.grid(index // WALL_COLUMNS, index % WALL_COLUMNS)
            self.empty_slots.append(slot)

        active_in_group = [udid for udid in ordered if udid in self.active_udids]
        if self.master_udid not in active_in_group:
            self.master_udid = active_in_group[0] if active_in_group else None
        master_session = self.sessions.get(self.master_udid) if self.master_udid else None
        self.master_view.set_session(master_session)
        for udid, tile in self.tiles.items():
            tile.set_master(udid == self.master_udid)
        self._refresh_group_button()

    def _rebuild_tiles_without_flash(self) -> None:
        """Prepare changed slots below the visible widgets, then swap in one Tk turn."""
        ordered = self._current_group_udids()
        tile_width, tile_height = TILE_VIEW_SIZE
        group_number = self._current_group_index() + 1
        connected_by_slot = dict(
            self.group_store.positioned_devices(self.sessions, group_number)
        )
        old_items = dict(getattr(self, "wall_items", {}))
        new_items: dict[int, DeviceTile | EmptySlot] = {}
        new_tiles: dict[str, DeviceTile] = {}
        new_empty_slots: list[EmptySlot] = []
        replacements: list[
            tuple[DeviceTile | EmptySlot, DeviceTile | EmptySlot | None]
        ] = []

        for index in range(GROUP_SIZE):
            slot_number = index + 1
            udid = connected_by_slot.get(slot_number)
            assigned_udid = self.group_store.udid_at(group_number, slot_number)
            assigned_label = (
                self.group_store.label(assigned_udid) if assigned_udid else ""
            )
            old_item = old_items.get(index)

            if (
                udid is not None
                and isinstance(old_item, DeviceTile)
                and old_item.session.udid == udid
                and old_item.session is self.sessions.get(udid)
            ):
                item: DeviceTile | EmptySlot = old_item
            elif (
                udid is None
                and isinstance(old_item, EmptySlot)
                and old_item.assigned_label == assigned_label
            ):
                item = old_item
            elif udid is not None:
                item = DeviceTile(
                    self,
                    self.wall,
                    self.sessions[udid],
                    index,
                    tile_width,
                    tile_height,
                )
                item.grid(index // WALL_COLUMNS, index % WALL_COLUMNS)
                if old_item is not None:
                    item.frame.lower(old_item.frame)
                replacements.append((item, old_item))
            else:
                item = EmptySlot(self.wall, index, assigned_label)
                item.grid(index // WALL_COLUMNS, index % WALL_COLUMNS)
                if old_item is not None:
                    item.frame.lower(old_item.frame)
                replacements.append((item, old_item))

            new_items[index] = item
            if isinstance(item, DeviceTile):
                new_tiles[item.session.udid] = item
            else:
                new_empty_slots.append(item)

        if replacements:
            # Geometry and first image are prepared while every replacement is
            # still underneath its old slot.  No empty background is exposed.
            self.root.update_idletasks()
            for item, _old_item in replacements:
                if isinstance(item, DeviceTile):
                    item.refresh()
            for item, old_item in replacements:
                if old_item is not None:
                    item.frame.lift(old_item.frame)

        self.tiles = new_tiles
        self.empty_slots = new_empty_slots
        self.wall_items = new_items

        active_in_group = [udid for udid in ordered if udid in self.active_udids]
        if self.master_udid not in active_in_group:
            self.master_udid = active_in_group[0] if active_in_group else None
        master_session = self.sessions.get(self.master_udid) if self.master_udid else None
        self.master_view.set_session(master_session)
        for udid, tile in self.tiles.items():
            tile.set_master(udid == self.master_udid)
        self._refresh_group_button()

        retired = [
            item
            for index, item in old_items.items()
            if new_items.get(index) is not item
        ]
        if retired:
            def destroy_retired(items: list[DeviceTile | EmptySlot] = retired) -> None:
                for item in items:
                    try:
                        item.destroy()
                    except tk.TclError:
                        pass

            self.root.after_idle(destroy_retired)

    def select_master(self, session: DeviceSession) -> None:
        if session.udid not in self.active_udids:
            self.summary.set("这台手机尚未开始投屏")
            return
        self.master_udid = session.udid
        self.master_view.set_session(session)
        for udid, tile in self.tiles.items():
            tile.set_master(udid == self.master_udid)
        self.summary.set(f"已选择主控手机：{session.udid[-8:]}")

    def route_touch(
        self,
        source: DeviceSession,
        kind: int,
        x: float,
        y: float,
        *,
        from_master: bool,
    ) -> None:
        route_key = (source.udid, from_master)
        routes = getattr(self, "_active_touch_routes", None)
        if routes is None:
            routes = {}
            self._active_touch_routes = routes

        if kind == 1:
            # Close an earlier incomplete gesture on this same canvas before
            # replacing its target snapshot.
            previous = routes.pop(route_key, None)
            if previous is not None:
                for session in previous.targets:
                    session.send_touch(0, previous.x, previous.y)

            # 左侧小窗始终单机；只有右侧主控开启同步时才广播。
            if self.sync_enabled.get() and from_master:
                candidates = [source]
                candidates.extend(
                    self.sessions[udid]
                    for udid in self._current_group_udids()
                    if udid != source.udid
                    and udid in self.active_udids
                    and udid in self.selected_udids
                )
            else:
                candidates = [source]
            accepted_targets = tuple(
                session
                for session in candidates
                if session.send_touch(kind, x, y)
            )
            if accepted_targets:
                routes[route_key] = ActiveTouchRoute(accepted_targets, x, y)
            accepted = len(accepted_targets)
        else:
            route = routes.get(route_key)
            if route is not None:
                route.x = x
                route.y = y
                targets = route.targets
            else:
                targets = ()
            accepted = sum(
                1 for session in targets if session.send_touch(kind, x, y)
            )
            if kind == 0:
                routes.pop(route_key, None)
        # 鼠标按下、移动和松开都是高频事件。正常发送时不改Tk状态文字，
        # 避免右侧分组下拉框和按钮跟随每次操作反复重绘、闪动。
        if accepted == 0 and kind in (0, 1):
            self.summary.set(
                "TrollVNC触控未发送：请确认手机已安装并启用TrollVNC"
            )

    def _keyboard_targets(
        self, source: DeviceSession, *, from_master: bool
    ) -> list[DeviceSession]:
        # 与鼠标路由保持一致：左侧小窗只控制本机，右侧主控才可同步。
        if not (self.sync_enabled.get() and from_master):
            return [source]
        targets = [source]
        targets.extend(
            self.sessions[udid]
            for udid in self._current_group_udids()
            if udid != source.udid
            and udid in self.active_udids
            and udid in self.selected_udids
        )
        return targets

    def activate_ime(
        self,
        source: DeviceSession,
        *,
        from_master: bool,
        screen_x: int,
        screen_y: int,
    ) -> None:
        if source.udid not in self.active_udids:
            return
        self._ime_source = source
        self._ime_from_master = from_master
        self.ime_worker.activate(screen_x + 8, screen_y + 28)

    def _route_ime_text(self, text: str) -> None:
        source = self._ime_source
        if source is None or source.udid not in self.active_udids or not text:
            return
        targets = self._keyboard_targets(
            source,
            from_master=self._ime_from_master,
        )
        accepted = sum(1 for session in targets if session.send_text(text))
        if accepted == 0:
            self.summary.set("键盘输入未发送：请确认该手机正在投屏")

    def _route_ime_key(self, page: int, usage: int, _label: str) -> None:
        source = self._ime_source
        if source is None or source.udid not in self.active_udids:
            return
        targets = self._keyboard_targets(
            source,
            from_master=self._ime_from_master,
        )
        accepted = sum(
            1 for session in targets if session.send_key(page, usage, 0)
        )
        if accepted == 0:
            self.summary.set("键盘按键未发送：请确认该手机正在投屏")

    def route_keypress(
        self,
        source: DeviceSession,
        keysym: str,
        char: str,
        *,
        from_master: bool,
    ) -> str | None:
        # Printable characters use the Unicode HID path one character at a
        # time.  Third-party iOS editors (notably Baidu Express) can ignore
        # synthetic keyboard scan codes while still accepting Unicode HID
        # events.  This remains direct typing: there is no visible PC input
        # box, clipboard mutation, or foreground tweak involved.
        if len(char) == 1 and char.isprintable():
            targets = self._keyboard_targets(source, from_master=from_master)
            accepted = sum(1 for session in targets if session.send_text(char))
            if accepted == 0:
                self.summary.set("键盘输入未发送：请确认该手机正在投屏")
            return "break"
        stroke = map_keypress(keysym, char)
        if stroke is None:
            return "break"
        targets = self._keyboard_targets(source, from_master=from_master)
        accepted = sum(
            1
            for session in targets
            if session.send_key(stroke.page, stroke.usage, stroke.modifiers)
        )
        # 正常打字不刷新状态文本，避免每个按键制造Tk字符串和界面抖动。
        if accepted == 0:
            self.summary.set("键盘输入未发送：请确认该手机正在投屏")
        return "break"

    def route_clipboard_paste(
        self, source: DeviceSession, *, from_master: bool
    ) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.summary.set("电脑剪贴板里没有可粘贴的文字")
            return
        if not text:
            self.summary.set("电脑剪贴板里没有可粘贴的文字")
            return
        targets = self._keyboard_targets(source, from_master=from_master)
        stripped = text.strip()
        is_short_numeric_code = (
            OTP_PASTE_PROFILE
            and stripped.isascii()
            and stripped.isdigit()
            and 4 <= len(stripped) <= 8
        )
        if is_short_numeric_code:
            if TROLLVNC_OTP_PASTE_PROFILE:
                accepted = sum(
                    1
                    for session in targets
                    if session.send_trollvnc_paste(stripped)
                )
                self.summary.set(
                    f"已向 {accepted}/{len(targets)} 台用TrollVNC原生粘贴验证码"
                )
            else:
                accepted = sum(
                    1 for session in targets if session.send_text_sequence(stripped)
                )
                self.summary.set(
                    f"已向 {accepted}/{len(targets)} 台逐位粘贴验证码"
                )
            return
        accepted = sum(1 for session in targets if session.send_text(text))
        self.summary.set(f"已向 {accepted}/{len(targets)} 台直接粘贴文字")

    def refresh_tiles(self) -> None:
        if SMOOTH_RENDER_PROFILE:
            # The old loop resized all ten phones in one Tk callback.  On an
            # older dual-socket CPU that blocks mouse delivery for tens of
            # milliseconds.  Refresh the master first, then spread two tiles
            # at a time across short callbacks.  Every tile is still sampled
            # every 50 ms, faster than the phone's 12 fps source cadence.
            self.master_view.refresh()
            tiles = list(self.tiles.values())
            if tiles:
                start = self._render_tile_cursor % len(tiles)
                count = min(RENDER_TILES_PER_SLICE, len(tiles))
                for offset in range(count):
                    tiles[(start + offset) % len(tiles)].refresh()
                self._render_tile_cursor = (start + count) % len(tiles)
            self.root.after(RENDER_SLICE_INTERVAL_MS, self.refresh_tiles)
            return
        for tile in list(self.tiles.values()):
            tile.refresh()
        self.master_view.refresh()
        self.root.after(DISPLAY_INTERVAL_MS, self.refresh_tiles)

    def periodic_scan(self) -> None:
        self.scan_devices()
        self.root.after(2000, self.periodic_scan)

    def refresh_health(self) -> None:
        active_sessions = [
            self.sessions[udid]
            for udid in sorted(self.active_udids)
            if udid in self.sessions
        ]
        stats = [session.stats() for session in active_sessions]
        total_fps = sum(item.fps for item in stats)
        touch_online = sum(1 for item in stats if item.touch_online)
        reconnects = sum(item.reconnects for item in stats)
        decode_errors = sum(item.decode_errors for item in stats)
        dropped_frames = sum(item.phone_dropped_frames for item in stats)
        ages = [item.last_frame_age for item in stats if item.last_frame_age is not None]
        max_age_ms = max(ages, default=0.0) * 1000
        memory_mb = working_set_mb()
        cpu_percent = self.load_sampler.sample_percent()
        self.health.set(
            f"投屏 {len(stats)} 台 · TrollVNC触控 {touch_online}/{len(stats)}"
            f" · 总帧率 {total_fps:.1f}"
            f" · CPU {cpu_percent:.0f}% · 内存 {memory_mb:.0f} MB"
        )
        self.health_tick += 1
        if self.health_tick % 10 == 0:
            LOGGER.info(
                "health devices=%s trollvnc_touch=%s total_fps=%.1f cpu=%.1f%% memory=%.1fMB gc_objects=%s py_threads=%s reconnects=%s decode_errors=%s max_frame_age=%.0fms",
                len(stats), touch_online, total_fps,
                cpu_percent, memory_mb,
                len(gc.get_objects()), threading.active_count(),
                reconnects, decode_errors, max_age_ms,
            )
        self.root.after(1000, self.refresh_health)

    def close(self) -> None:
        self._closing = True
        self._hide_group_popup()
        self._stop_music(reset=True)
        if self._usb_count_after_id is not None:
            try:
                self.root.after_cancel(self._usb_count_after_id)
            except tk.TclError:
                pass
            self._usb_count_after_id = None
        self.action_hub.close()
        self.ime_worker.close()
        self._active_touch_routes.clear()
        for session in self.sessions.values():
            session.stop()
        deadline = time.monotonic() + 2.0
        for session in [*self.sessions.values(), *self._retired_sessions]:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            session.wait_stopped(timeout=remaining)
        self.root.after(120, self.root.destroy)


def main() -> None:
    multiprocessing.freeze_support()
    if "--ime-worker" in sys.argv:
        sys.argv = [sys.argv[0], *(arg for arg in sys.argv[1:] if arg != "--ime-worker")]
        from xinglan.ime_worker import main as ime_worker_main

        ime_worker_main()
        return
    parser = argparse.ArgumentParser(
        description="星澜 H.264投屏 + TrollVNC独立触控版"
    )
    parser.add_argument("--max-devices", type=int, default=60)
    args = parser.parse_args()
    log_path = configure_logging(PROJECT_DIR)
    LOGGER.info(
        "application starting max_devices=%s decoder=software log=%s",
        args.max_devices,
        log_path,
    )
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Xinglan.USBControl"
            )
        except (AttributeError, OSError):
            LOGGER.warning("failed to set Windows application identity", exc_info=True)
    root = tk.Tk()
    XinglanApp(
        root,
        max(1, min(60, args.max_devices)),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
