from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from .bootstrap import configure_dependencies

configure_dependencies()

from PIL import Image  # noqa: E402

from .protocol import (
    VIDEO_HEADER_V3_SIZE,
    VIDEO_MAGIC_V3,
    VIDEO_PACKET_HEADER_SIZE,
    parse_video_header_v3,
    parse_video_packet_header,
    touch_message,
)

import av  # noqa: E402
from pymobiledevice3.service_connection import ServiceConnection  # noqa: E402


LOGGER = logging.getLogger("xinglan.session")


async def read_exact(connection: Any, count: int, timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("手机投屏通道超时")
        chunk = await asyncio.wait_for(connection.recv_any(count - len(data)), remaining)
        if not chunk:
            raise ConnectionError("手机投屏通道已断开")
        data.extend(chunk)
    return bytes(data)


class LatestFrame:
    """只保存最新画面；新帧直接覆盖旧帧，永不形成积压队列。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._image: Image.Image | None = None
        self._sequence = 0
        self._received_at = 0.0

    def publish(self, image: Image.Image) -> int:
        with self._lock:
            self._image = image
            self._sequence += 1
            self._received_at = time.monotonic()
            return self._sequence

    def snapshot(self) -> tuple[int, float, Image.Image | None]:
        with self._lock:
            return self._sequence, self._received_at, self._image


def prepare_xlv3_image(image: Image.Image) -> Image.Image:
    """XLV3/IOSurface already supplies an upright portrait frame."""
    return image


@dataclass(frozen=True)
class SessionStats:
    status: str
    fps: float
    reconnects: int
    last_frame_age: float | None
    error: str


class DeviceSession:
    VIDEO_PORT_NEW = 6202
    TOUCH_PORT = 6000

    def __init__(self, udid: str) -> None:
        self.udid = udid
        self.latest = LatestFrame()
        self._lock = threading.Lock()
        self._status = "等待连接"
        self._error = ""
        self._fps = 0.0
        self._reconnects = 0
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._touch_queue: asyncio.Queue[bytes] | None = None
        self._thread = threading.Thread(target=self._thread_main, name=f"device-{udid[-8:]}", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)

    def send_touch(self, kind: int, normalized_x: float, normalized_y: float) -> bool:
        loop = self._loop
        queue = self._touch_queue
        if not loop or not queue or not loop.is_running():
            return False
        message = touch_message(kind, normalized_x, normalized_y)

        def enqueue_latest() -> None:
            if queue.qsize() >= 32:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)

        loop.call_soon_threadsafe(enqueue_latest)
        return True

    def stats(self) -> SessionStats:
        sequence, received_at, _ = self.latest.snapshot()
        age = (time.monotonic() - received_at) if sequence and received_at else None
        with self._lock:
            return SessionStats(self._status, self._fps, self._reconnects, age, self._error)

    def _set_state(self, status: str, error: str = "") -> None:
        with self._lock:
            changed = status != self._status or error != self._error
            self._status = status
            self._error = error
        if changed:
            suffix = f" error={error}" if error else ""
            LOGGER.info("device=%s status=%s%s", self.udid, status, suffix)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._touch_queue = asyncio.Queue(maxsize=32)
        try:
            loop.run_until_complete(self._run())
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _run(self) -> None:
        video_task = asyncio.create_task(self._video_supervisor())
        touch_task = asyncio.create_task(self._touch_supervisor())
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.2)
        finally:
            video_task.cancel()
            touch_task.cancel()
            await asyncio.gather(video_task, touch_task, return_exceptions=True)

    async def _video_supervisor(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            connection = None
            try:
                self._set_state("正在连接视频")
                connection = await asyncio.wait_for(
                    ServiceConnection.create_using_usbmux(
                        self.udid, self.VIDEO_PORT_NEW, connection_type="USB"
                    ),
                    timeout=3.0,
                )
                # 一旦真正连通，下一次异常从快速重连重新开始，不沿用旧退避时间。
                delay = 0.5
                await self._receive_video(connection)
                raise ConnectionError("视频连接已结束")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._reconnects += 1
                self._set_state("视频重连中", str(exc))
                await asyncio.sleep(delay)
                delay = min(5.0, delay * 1.6)
            finally:
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass

    async def _receive_video(self, connection: Any) -> None:
        magic = await read_exact(connection, 4, 5.0)
        if magic != VIDEO_MAGIC_V3:
            raise ValueError(f"手机未运行星澜XLV3投屏服务：{magic!r}")
        header_bytes = magic + await read_exact(connection, VIDEO_HEADER_V3_SIZE - 4, 5.0)
        header = parse_video_header_v3(header_bytes)
        decoder = av.CodecContext.create("h264", "r")
        self._set_state(
            f"投屏中 {header.width}×{header.height} · 新IOSurface · {self.VIDEO_PORT_NEW}"
        )
        frame_count = 0
        window_started = time.monotonic()
        while not self._stop.is_set():
            packet = parse_video_packet_header(
                await read_exact(connection, VIDEO_PACKET_HEADER_SIZE, 3.5)
            )
            payload = (
                await read_exact(connection, packet.payload_length, 3.5)
                if packet.payload_length
                else b""
            )
            if packet.packet_type != 1:
                continue
            encoded = payload
            decoded = decoder.decode(av.Packet(encoded))
            for frame in decoded:
                image = prepare_xlv3_image(frame.to_image())
                self.latest.publish(image)
                frame_count += 1
            now = time.monotonic()
            elapsed = now - window_started
            if elapsed >= 2.0:
                with self._lock:
                    self._fps = frame_count / elapsed
                frame_count = 0
                window_started = now

    async def _touch_supervisor(self) -> None:
        assert self._touch_queue is not None
        connection = None
        pending: bytes | None = None
        while not self._stop.is_set():
            try:
                if pending is None:
                    pending = await asyncio.wait_for(self._touch_queue.get(), timeout=0.5)
                if connection is None:
                    connection = await asyncio.wait_for(
                        ServiceConnection.create_using_usbmux(self.udid, self.TOUCH_PORT, connection_type="USB"),
                        timeout=4.0,
                    )
                await asyncio.wait_for(connection.sendall(pending), timeout=1.0)
                pending = None
            except asyncio.TimeoutError:
                if pending is None:
                    continue
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass
                    connection = None
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass
                    connection = None
                await asyncio.sleep(0.2)
