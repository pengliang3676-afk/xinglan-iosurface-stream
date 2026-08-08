from __future__ import annotations

import argparse
import logging
import math
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from xinglan.bootstrap import PROJECT_DIR, configure_dependencies

configure_dependencies()

from PIL import Image, ImageDraw, ImageTk  # noqa: E402

from xinglan.device_discovery import discover_usb_udids  # noqa: E402
from xinglan.diagnostics import ProcessLoadSampler, configure_logging, working_set_mb  # noqa: E402
from xinglan.session import DeviceSession  # noqa: E402


TILE_WIDTH = 180
TILE_HEIGHT = 320
TILE_FRAME_WIDTH = TILE_WIDTH + 14
TILE_FRAME_HEIGHT = TILE_HEIGHT + 66
DISPLAY_INTERVAL_MS = 80
LOGGER = logging.getLogger("xinglan.app")


class DeviceTile:
    def __init__(self, owner: "XinglanApp", parent: tk.Widget, session: DeviceSession, index: int) -> None:
        self.owner = owner
        self.session = session
        self.index = index
        self.last_sequence = -1
        self.last_move_at = 0
        self.dragging = False
        self.photo: ImageTk.PhotoImage | None = None
        self.image_bounds = (0, 0, TILE_WIDTH, TILE_HEIGHT)

        # 固定卡片外框。帧率/延迟文字每秒变化时，不允许Tk重新计算卡片宽度。
        self.frame = tk.Frame(
            parent,
            width=TILE_FRAME_WIDTH,
            height=TILE_FRAME_HEIGHT,
            bg="#1d2939",
            highlightthickness=2,
            highlightbackground="#344054",
        )
        self.frame.pack_propagate(False)
        self.title = tk.Label(
            self.frame, text=f"手机 {index + 1:02d}  ·  {session.udid[-8:]}",
            bg="#1d2939", fg="white", anchor="w", font=("Microsoft YaHei UI", 10, "bold")
        )
        self.title.pack(fill="x", padx=6, pady=(5, 3))
        self.canvas = tk.Canvas(
            self.frame, width=TILE_WIDTH, height=TILE_HEIGHT, bg="black",
            highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack(padx=5)
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
        self.frame.grid(row=row, column=column, padx=5, pady=5, sticky="n")

    def destroy(self) -> None:
        self.frame.destroy()

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
        if image is None or sequence == self.last_sequence:
            return
        self.last_sequence = sequence
        self._show_image(image)

    def _show_placeholder(self, text: str) -> None:
        image = Image.new("RGB", (TILE_WIDTH, TILE_HEIGHT), "#101828")
        draw = ImageDraw.Draw(image)
        draw.text((TILE_WIDTH // 2, TILE_HEIGHT // 2), text, fill="#98a2b3", anchor="mm")
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.image_item, image=self.photo)

    def _show_image(self, image: Image.Image) -> None:
        scale = min(TILE_WIDTH / image.width, TILE_HEIGHT / image.height)
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        resized = image.resize((width, height), Image.Resampling.BILINEAR)
        surface = Image.new("RGB", (TILE_WIDTH, TILE_HEIGHT), "black")
        x = (TILE_WIDTH - width) // 2
        y = (TILE_HEIGHT - height) // 2
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


class XinglanApp:
    def __init__(self, root: tk.Tk, max_devices: int = 10) -> None:
        self.root = root
        self.max_devices = max_devices
        self.sessions: dict[str, DeviceSession] = {}
        self.tiles: dict[str, DeviceTile] = {}
        self.missing_scans: dict[str, int] = {}
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

        self.wall = tk.Frame(root, bg="#0b1220")
        self.wall.pack(fill="both", expand=True, padx=8, pady=4)

        self.scan_devices()
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
        columns = max(1, min(5, math.ceil(math.sqrt(max(1, len(ordered))))))
        for index, udid in enumerate(ordered):
            tile = DeviceTile(self, self.wall, self.sessions[udid], index)
            tile.grid(index // columns, index % columns)
            self.tiles[udid] = tile

    def route_touch(self, source: DeviceSession, kind: int, x: float, y: float) -> None:
        targets = list(self.sessions.values()) if self.sync_enabled.get() else [source]
        accepted = sum(1 for session in targets if session.send_touch(kind, x, y))
        if kind in (0, 1):
            self.summary.set(f"已向 {accepted}/{len(targets)} 台发送触摸 · 同步群控={'开' if self.sync_enabled.get() else '关'}")

    def refresh_tiles(self) -> None:
        for tile in list(self.tiles.values()):
            tile.refresh()
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
