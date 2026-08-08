from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(project_dir: Path) -> Path:
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "星澜新版运行.log"
    root = logging.getLogger("xinglan")
    if not root.handlers:
        root.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    return log_path


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def working_set_mb() -> float:
    if os.name != "nt":
        return 0.0
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    function = ctypes.windll.psapi.GetProcessMemoryInfo
    # Windows x64 下必须声明指针参数；否则伪句柄 -1 会按32位值传递并返回失败。
    function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    function.restype = wintypes.BOOL
    ok = function(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        return 0.0
    return counters.WorkingSetSize / (1024 * 1024)


class ProcessLoadSampler:
    """按Windows任务管理器口径估算本进程占全部逻辑处理器的百分比。"""

    def __init__(self) -> None:
        self._wall = time.monotonic()
        self._cpu = time.process_time()

    def sample_percent(self) -> float:
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        wall_delta = now_wall - self._wall
        cpu_delta = now_cpu - self._cpu
        self._wall = now_wall
        self._cpu = now_cpu
        if wall_delta <= 0:
            return 0.0
        logical_cpus = max(1, os.cpu_count() or 1)
        return max(0.0, (cpu_delta / wall_delta) * 100.0 / logical_cpus)


@dataclass
class StabilityMonitor:
    """Aggregate a long-running 10-device test without retaining per-second data."""

    duration_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    sample_count: int = 0
    device_min: int = 10**9
    device_max: int = 0
    fps_total: float = 0.0
    fps_min: float = float("inf")
    fps_max: float = 0.0
    cpu_total: float = 0.0
    cpu_max: float = 0.0
    memory_start: float | None = None
    memory_last: float = 0.0
    memory_max: float = 0.0
    reconnect_start: int | None = None
    reconnect_last: int = 0
    decode_error_start: int | None = None
    decode_error_last: int = 0
    dropped_start: int | None = None
    dropped_last: int = 0
    max_frame_age_ms: float = 0.0
    hardware_min: int = 10**9
    hardware_max: int = 0
    report_path: Path | None = None

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def complete(self) -> bool:
        return self.elapsed_seconds >= self.duration_seconds

    def record(
        self,
        *,
        devices: int,
        fps: float,
        cpu: float,
        memory_mb: float,
        reconnects: int,
        decode_errors: int,
        dropped_frames: int,
        max_frame_age_ms: float,
        hardware_decoders: int,
    ) -> None:
        self.sample_count += 1
        self.device_min = min(self.device_min, devices)
        self.device_max = max(self.device_max, devices)
        self.fps_total += fps
        self.fps_min = min(self.fps_min, fps)
        self.fps_max = max(self.fps_max, fps)
        self.cpu_total += cpu
        self.cpu_max = max(self.cpu_max, cpu)
        if self.memory_start is None:
            self.memory_start = memory_mb
        self.memory_last = memory_mb
        self.memory_max = max(self.memory_max, memory_mb)
        if self.reconnect_start is None:
            self.reconnect_start = reconnects
        self.reconnect_last = reconnects
        if self.decode_error_start is None:
            self.decode_error_start = decode_errors
        self.decode_error_last = decode_errors
        if self.dropped_start is None:
            self.dropped_start = dropped_frames
        self.dropped_last = dropped_frames
        self.max_frame_age_ms = max(self.max_frame_age_ms, max_frame_age_ms)
        self.hardware_min = min(self.hardware_min, hardware_decoders)
        self.hardware_max = max(self.hardware_max, hardware_decoders)

    def write_report(self, log_dir: Path, decoder_preference: str) -> Path:
        if self.report_path is not None:
            return self.report_path
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"十台稳定性报告_{datetime.now():%Y%m%d_%H%M%S}.txt"
        samples = max(1, self.sample_count)
        memory_start = self.memory_start or 0.0
        reconnect_start = self.reconnect_start or 0
        decode_error_start = self.decode_error_start or 0
        dropped_start = self.dropped_start or 0
        device_min = 0 if self.device_min == 10**9 else self.device_min
        hardware_min = 0 if self.hardware_min == 10**9 else self.hardware_min
        fps_min = 0.0 if self.fps_min == float("inf") else self.fps_min
        memory_growth = self.memory_last - memory_start
        elapsed_minutes = max(self.elapsed_seconds / 60.0, 1.0 / 60.0)
        memory_growth_per_minute = max(0.0, memory_growth) / elapsed_minutes
        memory_stable = memory_growth <= 64.0 and memory_growth_per_minute <= 2.0
        if not self.complete:
            verdict = "未完成"
        elif (
            device_min >= 10
            and self.reconnect_last - reconnect_start == 0
            and self.decode_error_last - decode_error_start == 0
            and self.max_frame_age_ms < 1500
            and memory_stable
        ):
            verdict = "通过"
        else:
            verdict = "需要检查"
        content = "\n".join(
            [
                "星澜 USB 原生群控 · 十台稳定性测试报告",
                f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                f"测试时长：{self.elapsed_seconds / 60:.1f} 分钟",
                f"采样次数：{self.sample_count}",
                f"判定：{verdict}",
                "",
                f"解码偏好：{decoder_preference}",
                f"在线设备：最低 {device_min} 台，最高 {self.device_max} 台",
                f"硬件解码：最低 {hardware_min} 台，最高 {self.hardware_max} 台",
                f"总帧率：平均 {self.fps_total / samples:.1f}，最低 {fps_min:.1f}，最高 {self.fps_max:.1f}",
                f"CPU：平均 {self.cpu_total / samples:.1f}%，最高 {self.cpu_max:.1f}%",
                f"内存：开始 {memory_start:.1f} MB，结束 {self.memory_last:.1f} MB，峰值 {self.memory_max:.1f} MB，增长 {memory_growth:+.1f} MB，平均每分钟 {memory_growth_per_minute:.2f} MB",
                f"重连增量：{self.reconnect_last - reconnect_start}",
                f"解码错误增量：{self.decode_error_last - decode_error_start}",
                f"手机端丢帧增量：{self.dropped_last - dropped_start}",
                f"最大画面延迟：{self.max_frame_age_ms:.0f} ms",
                "",
                "通过口径：全程至少10台、无新增重连、无新增解码错误、最大画面延迟低于1500ms、内存总增长不超过64MB且平均每分钟不超过2MB。",
            ]
        )
        path.write_text(content + "\n", encoding="utf-8")
        self.report_path = path
        return path
