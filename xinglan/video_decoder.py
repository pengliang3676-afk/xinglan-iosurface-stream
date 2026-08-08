from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .bootstrap import configure_dependencies


LOGGER = logging.getLogger("xinglan.video_decoder")
WINDOWS_NO_DIALOG_FLAGS = 0x0001 | 0x0002 | 0x8000


@dataclass
class H264Decoder:
    context: Any
    name: str
    hardware: bool

    def decode(self, payload: bytes) -> list[Any]:
        import av

        return self.context.decode(av.Packet(payload))


def create_h264_decoder(preference: str = "software") -> H264Decoder:
    """Create a low-latency H.264 decoder with a safe software fallback."""
    configure_dependencies()
    import av

    requested = (preference or "software").strip().lower()
    if requested not in {"", "none", "software"}:
        try:
            from av.codec.hwaccel import HWAccel

            context = av.CodecContext.create(
                "h264",
                "r",
                hwaccel=HWAccel(requested, allow_software_fallback=True),
            )
            context.thread_count = 1
            return H264Decoder(context, requested.upper(), True)
        except Exception as exc:
            LOGGER.warning("hardware decoder %s unavailable: %s", requested, exc)

    context = av.CodecContext.create("h264", "r")
    # 十台设备各自只保留最新帧；限制每个软解码器线程数，避免线程过量争抢。
    context.thread_count = 1
    return H264Decoder(context, "软件", False)


def probe_hardware_backend(project_dir: Path, timeout: float = 12.0) -> str:
    """Select a decoder backend; hardware decoding is an explicit experiment.

    The Tk display path needs every decoded frame in system memory.  On the
    current Windows/PyAV stack, repeatedly downloading D3D11VA frames caused
    native memory to grow during the ten-device soak test.  Software decoding
    already sustains the 10 x 12 fps target, so stability is the safe default.
    """
    override = os.environ.get("XINGLAN_HWACCEL", "software").strip().lower()
    if override in {"none", "off", "software"}:
        return "software"
    candidates = [override] if override not in {"", "auto"} else ["d3d11va", "dxva2"]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for candidate in candidates:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "xinglan.video_decoder", "--probe", candidate],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.warning("hardware probe %s failed to run: %s", candidate, exc)
            continue
        if completed.returncode == 0 and "PROBE_OK" in completed.stdout:
            LOGGER.info("hardware decoder selected: %s", candidate)
            return candidate
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        LOGGER.warning(
            "hardware probe %s rejected exit=%s detail=%s",
            candidate,
            completed.returncode,
            detail,
        )
    LOGGER.info("hardware decoder unavailable; using software decoder")
    return "software"


def _suppress_windows_crash_dialogs() -> None:
    if os.name == "nt":
        ctypes.windll.kernel32.SetErrorMode(WINDOWS_NO_DIALOG_FLAGS)


def _probe_decode(device_type: str) -> bool:
    """Encode and decode a short real H.264 stream to validate the driver path."""
    _suppress_windows_crash_dialogs()
    configure_dependencies()
    import av
    from PIL import Image

    encoder = av.CodecContext.create("libx264", "w")
    encoder.width = 360
    encoder.height = 640
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 12)
    encoder.framerate = Fraction(12, 1)
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency"}
    encoder.open()

    packets: list[bytes] = []
    for index, color in enumerate(((20, 40, 80), (40, 80, 120), (80, 120, 160))):
        frame = av.VideoFrame.from_image(Image.new("RGB", (360, 640), color))
        frame.pts = index
        packets.extend(bytes(packet) for packet in encoder.encode(frame))
    packets.extend(bytes(packet) for packet in encoder.encode(None))

    decoder = create_h264_decoder(device_type)
    if not decoder.hardware:
        return False
    decoded = []
    for packet in packets:
        decoded.extend(decoder.decode(packet))
    decoded.extend(decoder.context.decode(None))
    if not decoded:
        return False
    # 强制把硬件帧下载到系统内存，验证正式界面需要的转换路径。
    image = decoded[-1].to_image()
    return image.size == (360, 640)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="")
    args = parser.parse_args()
    if not args.probe:
        return 2
    try:
        if _probe_decode(args.probe):
            print(f"PROBE_OK {args.probe}", flush=True)
            return 0
    except Exception as exc:
        print(f"PROBE_FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
