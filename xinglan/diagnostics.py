from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
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
