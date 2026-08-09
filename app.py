from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from xinglan.bootstrap import PROJECT_DIR, configure_dependencies

configure_dependencies()

from PIL import Image, ImageDraw, ImageTk  # noqa: E402

from xinglan.device_discovery import discover_usb_udids_stable  # noqa: E402
from xinglan.device_actions import (  # noqa: E402
    identify_physical_device,
    PersistentDeviceActionHub,
)
from xinglan.device_groups import DeviceGroupStore  # noqa: E402
from xinglan.file_transfer import send_file_to_devices  # noqa: E402
from xinglan.diagnostics import (  # noqa: E402
    ProcessLoadSampler,
    StabilityMonitor,
    configure_logging,
    working_set_mb,
)
from xinglan.control_protocol import SystemAction  # noqa: E402
from xinglan.session import DeviceSession  # noqa: E402
from xinglan.video_decoder import probe_hardware_backend  # noqa: E402


WALL_COLUMNS = 5
WALL_ROWS = 2
GROUP_SIZE = WALL_COLUMNS * WALL_ROWS
TILE_VIEW_SIZE = (230, 408)
MASTER_VIEW_SIZE = (356, 633)
TOP_BAR_HEIGHT = 64
RIGHT_PANEL_WIDTH = 360
PHONE_HEAD_HEIGHT = 20
SIDE_RAIL_WIDTH = 36
WALL_GAP = 3
DISPLAY_INTERVAL_MS = 80
LOGGER = logging.getLogger("xinglan.app")


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
        self.last_move_at = 0
        self.dragging = False
        self.photo: ImageTk.PhotoImage | None = None
        self.render_size = (0, 0)
        self.image_bounds = (0, 0, tile_width, tile_height)

        # 固定卡片外框。帧率/延迟文字每秒变化时，不允许Tk重新计算卡片宽度。
        self.frame = tk.Frame(
            parent,
            bg="#1d2939",
            highlightthickness=1,
            highlightbackground="#344054",
        )
        # 复刻旧版星澜卡片：20px 标题栏在画面上方，36px 操作栏贯穿整卡。
        # 手机画面一直铺到卡片底部，状态不再额外占用一整行高度。
        self.title_row = tk.Frame(self.frame, bg="#050c16")
        self.title_row.place(
            x=0, y=0, relwidth=1, width=-SIDE_RAIL_WIDTH, height=PHONE_HEAD_HEIGHT
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
            highlightthickness=0, cursor="hand2"
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
        self.selection_check = tk.Checkbutton(
            self.side,
            variable=self.selected_var,
            command=lambda: self.owner.set_device_selected(
                self.session.udid, self.selected_var.get()
            ),
            bg="#111827",
            fg="white",
            activebackground="#111827",
            activeforeground="white",
            selectcolor="#2e90fa",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self.selection_check.pack(pady=(4, 0))
        tk.Label(
            self.side,
            text=f"{index + 1:02d}",
            bg="#111827",
            fg="white",
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(pady=(0, 6))
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
            font=("Microsoft YaHei UI", 8),
        )
        self.master_button.pack(fill="x", padx=3, pady=(0, 7))
        self.start_button = tk.Button(
            self.side, text="开始",
            command=lambda: self.owner.start_device(self.session.udid),
            bg="#1d2939", fg="white", relief="flat",
            padx=1, pady=4, font=("Microsoft YaHei UI", 8),
        )
        self.start_button.pack(fill="x", padx=3, pady=2)
        self.stop_button = tk.Button(
            self.side, text="停止",
            command=lambda: self.owner.stop_device(self.session.udid),
            bg="#1d2939", fg="white", relief="flat",
            padx=1, pady=4, font=("Microsoft YaHei UI", 8),
        )
        self.stop_button.pack(fill="x", padx=3, pady=2)
        tk.Button(
            self.side, text="掉线", state="disabled",
            bg="#1d2939", fg="#667085", relief="flat",
            padx=1, pady=4, font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=3, pady=2)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.status = tk.Label(
            self.frame, text="等待画面", bg="#1d2939", fg="#98a2b3",
            anchor="w", width=24, font=("Microsoft YaHei UI", 8)
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.set_connected(self.owner.is_session_active(self.session.udid))
        self._show_placeholder(
            "正在连接" if self.owner.is_session_active(self.session.udid) else "等待手动投屏"
        )

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
        self.start_button.configure(state="disabled" if connected else "normal")
        self.stop_button.configure(state="normal" if connected else "disabled")
        self.master_button.configure(state="normal" if connected else "disabled")

    def refresh(self) -> None:
        sequence, _, image = self.session.latest.snapshot()
        stats = self.session.stats()
        if stats.status.startswith("投屏中"):
            status_text = f"重连{stats.reconnects}"
        else:
            status_text = stats.status
        self.status.configure(
            text=status_text,
            fg="#12b76a" if stats.status.startswith("投屏中") else "#f79009",
        )
        self.set_connected(self.owner.is_session_active(self.session.udid))
        self.title.configure(
            text=f"{self.index + 1:02d} · {self.owner.device_label(self.session.udid)} · {status_text}"
        )
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
        image = Image.new("RGB", (self.tile_width, self.tile_height), "#101828")
        draw = ImageDraw.Draw(image)
        draw.text(
            (self.tile_width // 2, self.tile_height // 2),
            text,
            fill="#98a2b3",
            anchor="mm",
        )
        self._set_photo(image)

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
            target_size, Image.Resampling.LANCZOS
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
        self.owner.activate_keyboard_capture(event.x_root, event.y_root)
        self.dragging = True
        self.owner.route_touch(self.session, 1, *point, from_master=False)

    def _move(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        now = event.time
        if now - self.last_move_at < 35:
            return
        self.last_move_at = now
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 2, *point, from_master=False)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        self.dragging = False
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 0, *point, from_master=False)


class EmptySlot:
    def __init__(
        self,
        parent: tk.Widget,
        index: int,
        assigned_label: str = "",
    ) -> None:
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
            font=("Microsoft YaHei UI", 9, "bold")
        ).pack(pady=(5, 8))
        for label in ("主控", "开始", "停止", "掉线"):
            tk.Button(
                side, text=label, state="disabled",
                bg="#1d2939", fg="#667085", relief="flat",
                padx=1, pady=4, font=("Microsoft YaHei UI", 8),
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
        self.last_move_at = 0
        self.dragging = False
        self.photo: ImageTk.PhotoImage | None = None
        self.render_size = (0, 0)
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
            cursor="hand2",
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
        self._show_placeholder("请选择主控手机")

    def set_session(self, session: DeviceSession | None) -> None:
        if self.session is session:
            return
        self.session = session
        self.last_sequence = -1
        self.dragging = False
        if session is None:
            self.title.configure(text="主控大画面")
            self.status.configure(text="请选择主控手机", fg="#98a2b3")
            self._show_placeholder("请选择主控手机")
            return
        self.title.configure(text=f"主控大画面 · {self.owner.device_label(session.udid)}")
        self.status.configure(text="正在连接", fg="#f79009")

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
        self.status.configure(text=text, fg=color)
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
        image = Image.new("RGB", (width, height), "#050a11")
        draw = ImageDraw.Draw(image)
        draw.text((width // 2, height // 2), text, fill="#98a2b3", anchor="mm")
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
        scale = min(canvas_width / image.width, canvas_height / image.height)
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        resized = image if image.size == (width, height) else image.resize(
            (width, height), Image.Resampling.LANCZOS
        )
        surface = Image.new("RGB", (canvas_width, canvas_height), "black")
        x = (canvas_width - width) // 2
        y = (canvas_height - height) // 2
        surface.paste(resized, (x, y))
        self.image_bounds = (x, y, x + width, y + height)
        self._set_photo(surface)

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
        self.owner.activate_keyboard_capture(event.x_root, event.y_root)
        self.dragging = True
        self.owner.route_touch(self.session, 1, *point, from_master=True)

    def _move(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        if event.time - self.last_move_at < 35:
            return
        self.last_move_at = event.time
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 2, *point, from_master=True)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        self.dragging = False
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 0, *point, from_master=True)


class XinglanApp:
    def __init__(
        self,
        root: tk.Tk,
        max_devices: int = 10,
        decoder_backend: str = "software",
        stability_minutes: float = 0.0,
    ) -> None:
        self.root = root
        self.max_devices = max_devices
        self.max_groups = max(1, (max_devices + GROUP_SIZE - 1) // GROUP_SIZE)
        self.decoder_backend = decoder_backend
        self.group_store = DeviceGroupStore(
            PROJECT_DIR / "config" / "device_groups.json",
            max_groups=self.max_groups,
            group_size=GROUP_SIZE,
        )
        self.stability_monitor = (
            StabilityMonitor(stability_minutes * 60.0)
            if stability_minutes > 0
            else None
        )
        self.sessions: dict[str, DeviceSession] = {}
        self.active_udids: set[str] = set()
        self.selected_udids: set[str] = set()
        self.tiles: dict[str, DeviceTile] = {}
        self.empty_slots: list[EmptySlot] = []
        self.missing_scans: dict[str, int] = {}
        self.master_udid: str | None = None
        self.sync_enabled = tk.BooleanVar(value=False)
        self.summary = tk.StringVar(value="准备扫描USB手机")
        self.health = tk.StringVar(value="帧率 0 · 内存 0 MB · 重连 0")
        self.load_sampler = ProcessLoadSampler()
        self.health_tick = 0
        self.action_hub = PersistentDeviceActionHub()
        self._keyboard_buffer = tk.StringVar(value="")
        self._keyboard_pending = ""
        self._keyboard_flush_job: str | None = None
        self._keyboard_buffer_busy = False

        root.title("星澜 USB 原生群控 · 新版测试")
        root.configure(bg="#0b1220")
        root.geometry("1280x900")
        try:
            root.state("zoomed")
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self.close)

        # A one-pixel mapped Entry gives Windows IMEs a real text target while
        # keeping the old StarLan layout unchanged.  Clicking a phone view
        # focuses it; committed text is forwarded to the active phone field.
        self.keyboard_capture = tk.Entry(
            root,
            textvariable=self._keyboard_buffer,
            borderwidth=0,
            highlightthickness=0,
            takefocus=True,
        )
        self.keyboard_capture.place(x=-2, y=-2, width=1, height=1)
        self._keyboard_buffer.trace_add("write", self._keyboard_buffer_changed)
        self.keyboard_capture.bind("<Control-v>", self._paste_clipboard)
        self.keyboard_capture.bind("<Control-V>", self._paste_clipboard)
        self.keyboard_capture.bind(
            "<Return>", lambda _event: self._send_captured_key(0x07, 0x28, "回车")
        )
        self.keyboard_capture.bind(
            "<KP_Enter>", lambda _event: self._send_captured_key(0x07, 0x28, "回车")
        )
        self.keyboard_capture.bind(
            "<BackSpace>", lambda _event: self._send_captured_key(0x07, 0x2A, "退格")
        )

        # 严格复用旧版网页的 64px 顶栏和右侧 600px 操作区。
        toolbar = tk.Frame(root, bg="#1d2939", height=TOP_BAR_HEIGHT)
        toolbar.pack(fill="x", padx=10, pady=(8, 6))
        toolbar.pack_propagate(False)
        tk.Label(
            toolbar, text="星澜 USB 原生群控", bg="#1d2939", fg="white",
            font=("Microsoft YaHei UI", 18, "bold")
        ).place(x=12, y=0, height=TOP_BAR_HEIGHT)

        # 四个顶部按钮与右侧主控栏共用同一条左右边界。
        # 这样“全部开屏”的左边缘会和下方“全选”严格对齐。
        top_actions = tk.Frame(toolbar, bg="#1d2939")
        top_actions.place(
            relx=1,
            x=0,
            y=15,
            width=RIGHT_PANEL_WIDTH,
            height=34,
            anchor="ne",
        )
        top_actions.grid_rowconfigure(0, weight=1)
        for column in range(4):
            top_actions.grid_columnconfigure(column, weight=1, uniform="top-actions")

        # 状态文字独立放在按钮左侧，避免再把按钮挤窄。
        status_block = tk.Frame(toolbar, bg="#1d2939")
        status_block.place(
            relx=1,
            x=-(RIGHT_PANEL_WIDTH + 20),
            y=11,
            width=620,
            height=42,
            anchor="ne",
        )
        tk.Label(
            status_block, textvariable=self.summary, bg="#1d2939", fg="#d0d5dd",
            anchor="e", font=("Microsoft YaHei UI", 8),
        ).pack(fill="x")
        tk.Label(
            status_block, textvariable=self.health, bg="#1d2939", fg="#d0d5dd",
            anchor="e", font=("Microsoft YaHei UI", 8),
        ).pack(fill="x")

        for column, (text, command) in enumerate(
            (
                ("全部开屏", self.wake_all_devices),
                ("全部熄屏", self.sleep_all_devices),
                ("刷新设备", self.scan_devices),
                ("分组设置", self.open_group_settings),
            )
        ):
            tk.Button(
                top_actions, text=text, command=command,
                bg="#2e90fa", fg="white", activebackground="#1570ef",
                activeforeground="white", relief="flat", borderwidth=0,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 2, 0),
            )

        self.shell = tk.Frame(root, bg="#0b1220")
        self.shell.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.right_panel = tk.Frame(
            self.shell,
            width=RIGHT_PANEL_WIDTH,
            bg="#101828",
            highlightthickness=2,
            highlightbackground="#d58b00",
        )
        self.right_panel.pack(side="right", fill="y", padx=(7, 0))
        self.right_panel.pack_propagate(False)
        self.right_panel.grid_columnconfigure(0, weight=1)
        self.right_panel.grid_rowconfigure(1, weight=1)
        self.master_toolbar = tk.Frame(
            self.right_panel, height=34, bg="#050a11",
            highlightthickness=1, highlightbackground="#344054",
        )
        self.master_toolbar.grid(row=0, column=0, sticky="ew")
        tk.Button(
            self.master_toolbar, text="全选", command=self.select_all_devices,
            bg="#2e90fa", fg="white", relief="flat", padx=8,
        ).pack(side="left", fill="y", padx=(5, 3), pady=3)
        self.sync_button = tk.Button(
            self.master_toolbar,
            text="□ 同步群控",
            command=self.toggle_sync_control,
            bg="#2e90fa",
            fg="white",
            activebackground="#1570ef",
            activeforeground="white",
            relief="flat",
        )
        self.sync_button.pack(side="left", fill="both", expand=True, padx=2, pady=3)
        tk.Button(
            self.master_toolbar, text="全反", command=self.clear_device_selection,
            bg="#2e90fa", fg="white", relief="flat", padx=8,
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

        self.scan_devices()
        if not self.tiles and not self.empty_slots:
            self._rebuild_tiles()
        root.after(DISPLAY_INTERVAL_MS, self.refresh_tiles)
        root.after(1000, self.refresh_health)
        root.after(2000, self.periodic_scan)

    def _build_right_controls(self) -> None:
        """Build the right-side controls in the same order as the StarLan web UI."""
        self.controls = tk.Frame(self.right_panel, bg="#101828")
        self.controls.grid(row=2, column=0, sticky="ew")

        def fixed_row(parent: tk.Widget, *, bottom: int = 4) -> tk.Frame:
            row = tk.Frame(parent, bg="#101828", height=44)
            row.pack(fill="x", padx=8, pady=(4, bottom))
            row.pack_propagate(False)
            return row

        group_row = tk.Frame(self.controls, bg="#101828")
        group_row.configure(height=44)
        group_row.pack(fill="x", padx=8, pady=(8, 4))
        group_row.pack_propagate(False)
        self.group_var = tk.StringVar(value="第1组")
        self.group_combo = ttk.Combobox(
            group_row, textvariable=self.group_var,
            values=["第1组"], state="readonly", width=7,
        )
        self.group_combo.pack(side="left", fill="y", padx=(0, 7))
        self.group_combo.bind("<<ComboboxSelected>>", self._group_changed)
        self.group_button = tk.Button(
            group_row, text="连接本组", command=self.toggle_current_group,
            bg="#12b76a", fg="white", relief="flat", borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.group_button.pack(side="left", fill="both", expand=True)

        # 中文输入后续由不可见的键盘接收器承载；右侧不再额外占两行，
        # 以保持和旧版网页完全一致的主控画面高度。
        self.text_input_var = tk.StringVar()

        for text, command in (
            ("切换窗口", self.switch_window),
            ("返回主屏", lambda: self.route_system_action(SystemAction.HOME)),
            ("文件传输", self.open_file_transfer),
        ):
            row = fixed_row(self.controls)
            tk.Button(
                row, text=text, command=command,
                bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
                font=("Microsoft YaHei UI", 10),
            ).pack(fill="both", expand=True)

        mode_row = tk.Frame(self.controls, bg="#101828", height=44)
        mode_row.pack(fill="x", padx=8, pady=(4, 8))
        mode_row.pack_propagate(False)
        tk.Button(
            mode_row, text="全部投屏",
            command=self.start_current_group,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Button(
            mode_row, text="全部停屏",
            command=self.stop_current_group,
            bg="#2e90fa", fg="white", relief="flat", borderwidth=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left", fill="both", expand=True, padx=(4, 0))

    def activate_keyboard_capture(
        self,
        screen_x: int | None = None,
        screen_y: int | None = None,
    ) -> None:
        """Route keyboard input and anchor the IME candidate by the click."""
        if screen_x is not None and screen_y is not None:
            local_x = max(1, min(self.root.winfo_width() - 2,
                                 screen_x - self.root.winfo_rootx()))
            local_y = max(1, min(self.root.winfo_height() - 2,
                                 screen_y - self.root.winfo_rooty()))
            self.keyboard_capture.place(x=local_x, y=local_y, width=1, height=1)
        self.root.after_idle(self.keyboard_capture.focus_set)

    def _keyboard_buffer_changed(self, *_args) -> None:
        if self._keyboard_buffer_busy:
            return
        text = self._keyboard_buffer.get()
        if not text:
            return
        self._keyboard_buffer_busy = True
        try:
            self._keyboard_buffer.set("")
        finally:
            self._keyboard_buffer_busy = False
        self._keyboard_pending += text
        if self._keyboard_flush_job is not None:
            self.root.after_cancel(self._keyboard_flush_job)
        # Coalesce fast English typing while preserving a committed Chinese
        # IME candidate as one Unicode payload.
        self._keyboard_flush_job = self.root.after(45, self._flush_keyboard_text)

    def _flush_keyboard_text(self) -> None:
        self._keyboard_flush_job = None
        text = self._keyboard_pending
        self._keyboard_pending = ""
        if text:
            self.send_text_input(text)

    def _paste_clipboard(self, _event: tk.Event | None = None) -> str:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.summary.set("电脑剪贴板中没有可粘贴的文字")
            return "break"
        if text:
            self._flush_keyboard_text()
            self.send_text_input(text)
        return "break"

    def _send_captured_key(self, page: int, usage: int, label: str) -> str:
        self._flush_keyboard_text()
        self.send_key_event(page, usage, label)
        return "break"

    def send_text_input(self, text: str | None = None) -> None:
        if text is None:
            text = self.text_input_var.get()
        if not text:
            self.summary.set("请输入要发送的文字")
            return
        targets = self._selected_control_udids()
        if not targets:
            self.summary.set("请先选择主控手机，或开启同步后勾选手机")
            return
        accepted = sum(
            1 for udid in targets
            if self.sessions[udid].send_text(text)
        )
        self.summary.set(f"文字已发送到 {accepted}/{len(targets)} 台手机")

    def send_key_event(self, page: int, usage: int, label: str) -> None:
        targets = self._selected_control_udids()
        if not targets:
            self.summary.set("请先选择主控手机，或开启同步后勾选手机")
            return
        accepted = sum(
            1 for udid in targets
            if self.sessions[udid].send_key(page, usage)
        )
        self.summary.set(f"{label}已发送到 {accepted}/{len(targets)} 台手机")

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

    def _run_all_device_action(self, action: str, label: str) -> None:
        udids = self._all_online_udids()
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

        future = self.action_hub.broadcast(udids, action)

        def finished(result_future) -> None:
            try:
                result = result_future.result()
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                result = exc
            try:
                self.root.after(0, lambda: completed(result))
            except tk.TclError:
                pass

        future.add_done_callback(finished)

    def wake_all_devices(self) -> None:
        self._run_all_device_action("wake", "全部开屏")

    def sleep_all_devices(self) -> None:
        self._run_all_device_action("sleep", "全部熄屏")

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
        self.sync_button.configure(text="☑ 同步群控" if enabled else "□ 同步群控")
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
            tile.selected_var.set(udid in self.selected_udids)

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
        self.group_combo.configure(values=values)
        self.group_var.set(values[current])

    def _group_changed(self, _event: tk.Event | None = None) -> None:
        self.master_udid = None
        self._rebuild_tiles()
        self.summary.set(
            f"已切换到{self.group_var.get()}，点击连接本组后才开始投屏"
        )

    def _refresh_group_button(self) -> None:
        connected = any(udid in self.active_udids for udid in self._current_group_udids())
        self.group_button.configure(
            text="断开本组" if connected else "连接本组",
            bg="#912f20" if connected else "#12b76a",
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

    def _stop_udids(self, udids: list[str]) -> int:
        stopped = 0
        for udid in udids:
            if udid not in self.active_udids:
                continue
            old_session = self.sessions.get(udid)
            if old_session is not None:
                old_session.stop()
                # 会话线程不可复用；保留USB设备卡片，但换成全新的待机会话。
                self.sessions[udid] = DeviceSession(
                    udid, decoder_preference=self.decoder_backend
                )
            self.active_udids.discard(udid)
            stopped += 1
            LOGGER.info("manual projection stopped: %s", udid)
        if stopped:
            # 给异步线程一点时间关闭USB通道，再主动回收PIL/Tk/PyAV对象。
            self.root.after(800, self._collect_released_resources)
        return stopped

    def _collect_released_resources(self) -> None:
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

    def route_system_action(self, action: SystemAction) -> None:
        target_udids = self._selected_control_udids()
        if not target_udids:
            self.summary.set("请先选择主控手机，或开启同步后勾选手机")
            return
        targets = [self.sessions[udid] for udid in target_udids]
        accepted = sum(1 for session in targets if session.send_system_action(action))
        self.summary.set(f"已向 {accepted}/{len(targets)} 台发送系统操作")

    def switch_window(self) -> None:
        self.route_system_action(SystemAction.APP_SWITCHER)

    def open_file_transfer(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("传输电脑文件到手机")
        window.geometry("560x330")
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
        selected_text = tk.StringVar(value="尚未选择文件")
        target_text = tk.StringVar()
        progress_text = tk.StringVar(value="请选择文件后开始传输")
        import_photo = tk.BooleanVar(value=False)
        transferring = tk.BooleanVar(value=False)

        def current_targets() -> list[str]:
            return self._selected_control_udids()

        def refresh_target_text() -> None:
            targets = current_targets()
            if targets:
                labels = "、".join(self.device_label(udid) for udid in targets[:5])
                if len(targets) > 5:
                    labels += f" 等{len(targets)}台"
                target_text.set(f"接收目标：{labels}")
            else:
                target_text.set("接收目标：尚未选择主控或同步手机")

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

        def close_window() -> None:
            if transferring.get():
                return
            window.destroy()

        def start_transfer() -> None:
            path_text = selected_path.get()
            if not path_text:
                progress_text.set("请先选择一个文件")
                return
            path = Path(path_text)
            if not path.is_file():
                progress_text.set("所选文件已经不存在")
                return
            targets = current_targets()
            refresh_target_text()
            if not targets:
                progress_text.set("没有接收手机：请选择主控，或开启同步并勾选手机")
                return
            should_import_photo = import_photo.get()
            transferring.set(True)
            choose_button.configure(state="disabled")
            start_button.configure(state="disabled", text="正在传输…")
            close_button.configure(state="disabled")
            progress_text.set(f"正在向 {len(targets)} 台手机传输，请稍候…")
            self.summary.set(
                f"正在向 {len(targets)} 台手机传输 {path.name}…"
            )

            def completed(result) -> None:
                transferring.set(False)
                if isinstance(result, Exception):
                    self.summary.set(f"文件传输失败：{result}")
                    if window.winfo_exists():
                        progress_text.set(f"传输失败：{result}")
                        choose_button.configure(state="normal")
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
        tk.Label(
            file_row,
            textvariable=selected_text,
            bg="#101828",
            fg="#d0d5dd",
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=12)
        tk.Checkbutton(
            window,
            text="图片或视频传完后导入系统“照片”",
            variable=import_photo,
            bg="#101828",
            fg="white",
            activebackground="#101828",
            activeforeground="white",
            selectcolor="#2e90fa",
        ).pack(anchor="w", padx=20, pady=(18, 8))
        tk.Label(
            window,
            textvariable=target_text,
            bg="#101828",
            fg="#d0d5dd",
            anchor="w",
        ).pack(fill="x", padx=20, pady=4)
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

    def scan_devices(self) -> None:
        try:
            udids = discover_usb_udids_stable(PROJECT_DIR)[: self.max_devices]
        except Exception as exc:
            self.summary.set(f"USB扫描失败：{exc}")
            LOGGER.warning("USB scan failed: %s", exc)
            return

        current = set(udids)
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
                self.sessions.pop(udid).stop()
                self.active_udids.discard(udid)
                self.missing_scans.pop(udid, None)
                changed = True

        for udid in udids:
            if udid in self.sessions:
                continue
            session = DeviceSession(udid, decoder_preference=self.decoder_backend)
            self.sessions[udid] = session
            # 与旧版一致：新出现在当前界面的手机默认处于勾选状态。
            self.selected_udids.add(udid)
            self.missing_scans[udid] = 0
            LOGGER.info("USB device discovered, waiting for manual projection: %s", udid)
            changed = True

        # Prepare lightweight control-only channels for every connected phone.
        # This does not start projection or decoding; it only restores the
        # legacy instant all-wake/all-sleep behavior.
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
            f"发现 {len(stable_udids)} 台USB手机 · 已投屏 {len(self.active_udids)} 台 · 默认不自动投屏"
        )

    def _rebuild_tiles(self) -> None:
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
        # 与星澜网页一致：左侧小窗始终单机，只有右侧主控开启同步时才广播。
        if self.sync_enabled.get() and from_master:
            # 主控本机始终接收操作；同步副机只取当前组中已勾选且已投屏的手机。
            targets = [source]
            targets.extend(
                self.sessions[udid]
                for udid in self._current_group_udids()
                if udid != source.udid
                and udid in self.active_udids
                and udid in self.selected_udids
            )
        else:
            targets = [source]
        accepted = sum(1 for session in targets if session.send_touch(kind, x, y))
        if kind in (0, 1):
            sync_text = "开" if self.sync_enabled.get() and from_master else "关"
            self.summary.set(f"已向 {accepted}/{len(targets)} 台发送触摸 · 同步群控={sync_text}")

    def refresh_tiles(self) -> None:
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
        reconnects = sum(item.reconnects for item in stats)
        hardware_decoders = sum(1 for item in stats if item.hardware_decode)
        software_decoders = sum(
            1 for item in stats if item.decoder_backend == "软件"
        )
        decode_errors = sum(item.decode_errors for item in stats)
        dropped_frames = sum(item.phone_dropped_frames for item in stats)
        ages = [item.last_frame_age for item in stats if item.last_frame_age is not None]
        max_age_ms = max(ages, default=0.0) * 1000
        memory_mb = working_set_mb()
        cpu_percent = self.load_sampler.sample_percent()
        stability_text = ""
        if self.stability_monitor is not None:
            stability_text = (
                f" · 稳测 {self.stability_monitor.elapsed_seconds / 60:.0f}/"
                f"{self.stability_monitor.duration_seconds / 60:.0f}分"
            )
        self.health.set(
            f"投屏 {len(stats)} 台 · 总帧率 {total_fps:.1f} · 硬解 {hardware_decoders} · 软解 {software_decoders}"
            f" · CPU {cpu_percent:.0f}% · 内存 {memory_mb:.0f} MB"
            f" · 重连 {reconnects} · 解码错 {decode_errors}"
            f"{stability_text}"
        )
        if self.stability_monitor is not None and self.stability_monitor.report_path is None:
            self.stability_monitor.record(
                devices=len(stats),
                fps=total_fps,
                cpu=cpu_percent,
                memory_mb=memory_mb,
                reconnects=reconnects,
                decode_errors=decode_errors,
                dropped_frames=dropped_frames,
                max_frame_age_ms=max_age_ms,
                hardware_decoders=hardware_decoders,
            )
            if self.stability_monitor.complete:
                report = self.stability_monitor.write_report(
                    PROJECT_DIR / "logs", self.decoder_backend
                )
                self.summary.set(f"十台稳定性测试完成：{report.name}")
                LOGGER.info("stability test completed report=%s", report)
        self.health_tick += 1
        if self.health_tick % 10 == 0:
            LOGGER.info(
                "health devices=%s total_fps=%.1f hardware=%s software=%s cpu=%.1f%% memory=%.1fMB reconnects=%s decode_errors=%s max_frame_age=%.0fms",
                len(stats), total_fps, hardware_decoders, software_decoders,
                cpu_percent, memory_mb, reconnects, decode_errors, max_age_ms,
            )
        self.root.after(1000, self.refresh_health)

    def close(self) -> None:
        if (
            self.stability_monitor is not None
            and self.stability_monitor.sample_count
            and self.stability_monitor.report_path is None
        ):
            report = self.stability_monitor.write_report(
                PROJECT_DIR / "logs", self.decoder_backend
            )
            LOGGER.info("partial stability report=%s", report)
        for session in self.sessions.values():
            session.stop()
        self.action_hub.close()
        self.root.after(120, self.root.destroy)


def main() -> None:
    parser = argparse.ArgumentParser(description="星澜 USB 原生群控新版")
    parser.add_argument("--max-devices", type=int, default=60)
    parser.add_argument("--stability-minutes", type=float, default=0.0)
    args = parser.parse_args()
    log_path = configure_logging(PROJECT_DIR)
    decoder_backend = probe_hardware_backend(PROJECT_DIR)
    LOGGER.info(
        "application starting max_devices=%s decoder=%s log=%s",
        args.max_devices,
        decoder_backend,
        log_path,
    )
    root = tk.Tk()
    XinglanApp(
        root,
        max(1, min(60, args.max_devices)),
        decoder_backend=decoder_backend,
        stability_minutes=max(0.0, args.stability_minutes),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
