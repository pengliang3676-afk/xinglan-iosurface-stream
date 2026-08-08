from __future__ import annotations

import argparse
import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from xinglan.bootstrap import PROJECT_DIR, configure_dependencies

configure_dependencies()

from PIL import Image, ImageDraw, ImageTk  # noqa: E402

from xinglan.device_discovery import discover_usb_udids  # noqa: E402
from xinglan.diagnostics import ProcessLoadSampler, configure_logging, working_set_mb  # noqa: E402
from xinglan.session import DeviceSession  # noqa: E402


WALL_COLUMNS = 5
WALL_ROWS = 2
MASTER_VIEW_SIZE = (360, 640)
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
            highlightthickness=2,
            highlightbackground="#344054",
        )
        self.title_row = tk.Frame(self.frame, bg="#1d2939")
        self.title_row.pack(fill="x", padx=5, pady=(4, 2))
        self.title = tk.Label(
            self.title_row,
            text=f"{index + 1:02d} · {session.udid[-8:]}",
            bg="#1d2939",
            fg="white",
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.title.pack(side="left", fill="x", expand=True)
        self.master_button = tk.Button(
            self.title_row,
            text="主控",
            command=lambda: self.owner.select_master(self.session),
            bg="#344054",
            fg="white",
            activebackground="#d92d20",
            activeforeground="white",
            relief="flat",
            padx=4,
            pady=0,
            font=("Microsoft YaHei UI", 8),
        )
        self.master_button.pack(side="right")
        self.canvas = tk.Canvas(
            self.frame, width=tile_width, height=tile_height, bg="black",
            highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack(fill="both", expand=True, padx=4)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.status = tk.Label(
            self.frame, text="等待画面", bg="#1d2939", fg="#98a2b3",
            anchor="w", width=24, font=("Microsoft YaHei UI", 8)
        )
        self.status.pack(fill="x", padx=6, pady=4)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self._show_placeholder("正在连接")

    def grid(self, row: int, column: int) -> None:
        self.frame.grid(row=row, column=column, padx=2, pady=2, sticky="nsew")

    def destroy(self) -> None:
        self.frame.destroy()

    def set_master(self, active: bool) -> None:
        color = "#d92d20" if active else "#344054"
        border = "#d92d20" if active else "#344054"
        self.master_button.configure(bg=color)
        self.frame.configure(highlightbackground=border)

    def refresh(self) -> None:
        sequence, _, image = self.session.latest.snapshot()
        stats = self.session.stats()
        age_text = ""
        if stats.last_frame_age is not None:
            age_text = f" · 延迟 {stats.last_frame_age * 1000:.0f}ms"
        if stats.status.startswith("投屏中"):
            age_value = stats.last_frame_age * 1000 if stats.last_frame_age is not None else 0
            status_text = f"{stats.fps:.1f}fps · {age_value:.0f}ms · 重连{stats.reconnects}"
        else:
            status_text = stats.status
        self.status.configure(
            text=status_text,
            fg="#12b76a" if stats.status.startswith("投屏中") else "#f79009",
        )
        canvas_size = (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        if image is None or (sequence == self.last_sequence and canvas_size == self.render_size):
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
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _show_image(self, image: Image.Image) -> None:
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        self.render_size = (canvas_width, canvas_height)
        scale = min(canvas_width / image.width, canvas_height / image.height)
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        if image.size == (width, height):
            resized = image
        else:
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
        surface = Image.new("RGB", (canvas_width, canvas_height), "black")
        x = (canvas_width - width) // 2
        y = (canvas_height - height) // 2
        surface.paste(resized, (x, y))
        self.image_bounds = (x, y, x + width, y + height)
        self.photo = ImageTk.PhotoImage(surface)
        self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _normalized(self, event: tk.Event) -> tuple[float, float] | None:
        left, top, right, bottom = self.image_bounds
        if event.x < left or event.x >= right or event.y < top or event.y >= bottom:
            return None
        return (event.x - left) / (right - left), (event.y - top) / (bottom - top)

    def _press(self, event: tk.Event) -> None:
        point = self._normalized(event)
        if point is None:
            return
        self.dragging = True
        self.owner.route_touch(self.session, 1, *point)

    def _move(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        now = event.time
        if now - self.last_move_at < 35:
            return
        self.last_move_at = now
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 2, *point)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        self.dragging = False
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 0, *point)


class EmptySlot:
    def __init__(self, parent: tk.Widget, index: int) -> None:
        self.frame = tk.Frame(
            parent,
            bg="#050a11",
            highlightthickness=1,
            highlightbackground="#344054",
        )
        tk.Label(
            self.frame,
            text=f"位置 {index + 1:02d}",
            bg="#111827",
            fg="#667085",
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill="x", padx=4, pady=(3, 2))
        tk.Label(
            self.frame,
            text="未连接",
            bg="#050a11",
            fg="#667085",
            font=("Microsoft YaHei UI", 10),
        ).pack(fill="both", expand=True)

    def grid(self, row: int, column: int) -> None:
        self.frame.grid(row=row, column=column, padx=2, pady=2, sticky="nsew")

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
        width, height = MASTER_VIEW_SIZE
        self.image_bounds = (0, 0, width, height)

        self.frame = tk.Frame(
            parent,
            width=width + 12,
            height=height + 76,
            bg="#101828",
            highlightthickness=2,
            highlightbackground="#d58b00",
        )
        self.frame.pack_propagate(False)
        self.title = tk.Label(
            self.frame,
            text="主控大画面",
            bg="#050a11",
            fg="white",
            anchor="w",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.title.pack(fill="x", padx=5, pady=(4, 3))
        self.canvas = tk.Canvas(
            self.frame,
            width=width,
            height=height,
            bg="black",
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(padx=4)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.status = tk.Label(
            self.frame,
            text="请选择主控手机",
            bg="#101828",
            fg="#98a2b3",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(fill="x", padx=6, pady=5)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
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
        self.title.configure(text=f"主控大画面 · {session.udid[-8:]}")
        self.status.configure(text="正在连接", fg="#f79009")

    def refresh(self) -> None:
        if self.session is None:
            return
        sequence, _, image = self.session.latest.snapshot()
        stats = self.session.stats()
        age_value = stats.last_frame_age * 1000 if stats.last_frame_age is not None else 0
        if stats.status.startswith("投屏中"):
            text = f"{stats.fps:.1f}fps · {age_value:.0f}ms · 重连{stats.reconnects}"
            color = "#12b76a"
        else:
            text = stats.status
            color = "#f79009"
        self.status.configure(text=text, fg=color)
        if image is None or sequence == self.last_sequence:
            return
        self.last_sequence = sequence
        self._show_image(image)

    def _show_placeholder(self, text: str) -> None:
        width, height = MASTER_VIEW_SIZE
        image = Image.new("RGB", (width, height), "#050a11")
        draw = ImageDraw.Draw(image)
        draw.text((width // 2, height // 2), text, fill="#98a2b3", anchor="mm")
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _show_image(self, image: Image.Image) -> None:
        canvas_width, canvas_height = MASTER_VIEW_SIZE
        scale = min(canvas_width / image.width, canvas_height / image.height)
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        resized = image if image.size == (width, height) else image.resize(
            (width, height), Image.Resampling.LANCZOS
        )
        surface = Image.new("RGB", MASTER_VIEW_SIZE, "black")
        x = (canvas_width - width) // 2
        y = (canvas_height - height) // 2
        surface.paste(resized, (x, y))
        self.image_bounds = (x, y, x + width, y + height)
        self.photo = ImageTk.PhotoImage(surface)
        self.canvas.itemconfigure(self.image_item, image=self.photo)

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
        self.dragging = True
        self.owner.route_touch(self.session, 1, *point)

    def _move(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        if event.time - self.last_move_at < 35:
            return
        self.last_move_at = event.time
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 2, *point)

    def _release(self, event: tk.Event) -> None:
        if not self.dragging or self.session is None:
            return
        self.dragging = False
        point = self._normalized(event)
        if point is not None:
            self.owner.route_touch(self.session, 0, *point)


class XinglanApp:
    def __init__(self, root: tk.Tk, max_devices: int = 10) -> None:
        self.root = root
        self.max_devices = max_devices
        self.sessions: dict[str, DeviceSession] = {}
        self.tiles: dict[str, DeviceTile] = {}
        self.empty_slots: list[EmptySlot] = []
        self.missing_scans: dict[str, int] = {}
        self.master_udid: str | None = None
        self.sync_enabled = tk.BooleanVar(value=False)
        self.summary = tk.StringVar(value="准备扫描USB手机")
        self.health = tk.StringVar(value="帧率 0 · 内存 0 MB · 重连 0")
        self.load_sampler = ProcessLoadSampler()
        self.health_tick = 0

        root.title("星澜 USB 原生群控 · 新版测试")
        root.configure(bg="#0b1220")
        root.geometry("1280x900")
        root.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = tk.Frame(root, bg="#1d2939", height=54)
        toolbar.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(
            toolbar, text="星澜 USB 原生群控", bg="#1d2939", fg="white",
            font=("Microsoft YaHei UI", 18, "bold")
        ).pack(side="left", padx=12, pady=8)
        ttk.Checkbutton(toolbar, text="同步群控", variable=self.sync_enabled).pack(side="right", padx=12)
        ttk.Button(toolbar, text="刷新设备", command=self.scan_devices).pack(side="right", padx=6)
        tk.Label(
            toolbar, textvariable=self.health, bg="#1d2939", fg="#d0d5dd",
            font=("Microsoft YaHei UI", 9)
        ).pack(side="right", padx=12)

        tk.Label(
            root, textvariable=self.summary, bg="#0b1220", fg="#d0d5dd",
            anchor="w", font=("Microsoft YaHei UI", 10)
        ).pack(fill="x", padx=14, pady=(0, 4))

        self.shell = tk.Frame(root, bg="#0b1220")
        self.shell.pack(fill="both", expand=True, padx=8, pady=4)
        self.right_panel = tk.Frame(self.shell, width=374, bg="#0b1220")
        self.right_panel.pack(side="right", fill="y", padx=(7, 0))
        self.right_panel.pack_propagate(False)
        self.master_view = MasterView(self, self.right_panel)
        self.master_view.frame.pack(fill="both", expand=True)
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

    def scan_devices(self) -> None:
        try:
            udids = discover_usb_udids(PROJECT_DIR)[: self.max_devices]
        except Exception as exc:
            self.summary.set(f"USB扫描失败：{exc}")
            LOGGER.warning("USB scan failed: %s", exc)
            return

        current = set(udids)
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
                self.missing_scans.pop(udid, None)
                changed = True

        for udid in udids:
            if udid in self.sessions:
                continue
            session = DeviceSession(udid)
            self.sessions[udid] = session
            self.missing_scans[udid] = 0
            session.start()
            LOGGER.info("device added: %s", udid)
            changed = True

        if changed:
            self._rebuild_tiles()
        retained = len(self.sessions) - len(udids)
        retained_text = f" · 短暂失联保留 {retained} 台" if retained > 0 else ""
        self.summary.set(
            f"发现 {len(udids)} 台USB手机{retained_text} · 视频只保留最新帧 · 上限 {self.max_devices} 台"
        )

    def _rebuild_tiles(self) -> None:
        ordered = sorted(self.sessions)
        for tile in self.tiles.values():
            tile.destroy()
        self.tiles.clear()
        for slot in self.empty_slots:
            slot.destroy()
        self.empty_slots.clear()
        tile_width, tile_height = 160, 284
        for index, udid in enumerate(ordered):
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

        visible_slots = min(self.max_devices, WALL_COLUMNS * WALL_ROWS)
        for index in range(len(ordered), visible_slots):
            slot = EmptySlot(self.wall, index)
            slot.grid(index // WALL_COLUMNS, index % WALL_COLUMNS)
            self.empty_slots.append(slot)

        if self.master_udid not in self.sessions:
            self.master_udid = ordered[0] if ordered else None
        master_session = self.sessions.get(self.master_udid) if self.master_udid else None
        self.master_view.set_session(master_session)
        for udid, tile in self.tiles.items():
            tile.set_master(udid == self.master_udid)

    def select_master(self, session: DeviceSession) -> None:
        self.master_udid = session.udid
        self.master_view.set_session(session)
        for udid, tile in self.tiles.items():
            tile.set_master(udid == self.master_udid)
        self.summary.set(f"已选择主控手机：{session.udid[-8:]}")

    def route_touch(self, source: DeviceSession, kind: int, x: float, y: float) -> None:
        targets = list(self.sessions.values()) if self.sync_enabled.get() else [source]
        accepted = sum(1 for session in targets if session.send_touch(kind, x, y))
        if kind in (0, 1):
            self.summary.set(f"已向 {accepted}/{len(targets)} 台发送触摸 · 同步群控={'开' if self.sync_enabled.get() else '关'}")

    def refresh_tiles(self) -> None:
        for tile in list(self.tiles.values()):
            tile.refresh()
        self.master_view.refresh()
        self.root.after(DISPLAY_INTERVAL_MS, self.refresh_tiles)

    def periodic_scan(self) -> None:
        self.scan_devices()
        self.root.after(2000, self.periodic_scan)

    def refresh_health(self) -> None:
        stats = [session.stats() for session in self.sessions.values()]
        total_fps = sum(item.fps for item in stats)
        reconnects = sum(item.reconnects for item in stats)
        memory_mb = working_set_mb()
        cpu_percent = self.load_sampler.sample_percent()
        self.health.set(
            f"总帧率 {total_fps:.1f} · CPU {cpu_percent:.0f}% · 内存 {memory_mb:.0f} MB · 重连 {reconnects}"
        )
        self.health_tick += 1
        if self.health_tick % 10 == 0:
            ages = [item.last_frame_age for item in stats if item.last_frame_age is not None]
            max_age_ms = max(ages, default=0.0) * 1000
            LOGGER.info(
                "health devices=%s total_fps=%.1f cpu=%.1f%% memory=%.1fMB reconnects=%s max_frame_age=%.0fms",
                len(stats), total_fps, cpu_percent, memory_mb, reconnects, max_age_ms,
            )
        self.root.after(1000, self.refresh_health)

    def close(self) -> None:
        for session in self.sessions.values():
            session.stop()
        self.root.after(120, self.root.destroy)


def main() -> None:
    parser = argparse.ArgumentParser(description="星澜 USB 原生群控新版")
    parser.add_argument("--max-devices", type=int, default=10)
    args = parser.parse_args()
    log_path = configure_logging(PROJECT_DIR)
    LOGGER.info("application starting max_devices=%s log=%s", args.max_devices, log_path)
    root = tk.Tk()
    XinglanApp(root, max(1, min(60, args.max_devices)))
    root.mainloop()


if __name__ == "__main__":
    main()
