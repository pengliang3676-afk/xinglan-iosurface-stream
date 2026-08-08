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

from .control_protocol import (  # noqa: E402
    CONTROL_MAGIC,
    CONTROL_PORT,
    HEADER,
    STATUS_MAGIC,
    STATUS_PORT,
    DeviceStatus,
    MessageHeader,
    MessageType,
    KeyCommand,
    SystemAction,
    TouchCommand,
    TouchPhase,
    pack_hello,
    pack_keyframe_request,
    pack_key_event,
    pack_ping,
    pack_system_action,
    pack_text_input,
    pack_touch,
    unpack_ack,
    unpack_device_status,
    unpack_header,
    unpack_hello,
)
from .protocol import (  # noqa: E402
    VIDEO_HEADER_V3_SIZE,
    VIDEO_MAGIC_V3,
    VIDEO_PACKET_HEADER_SIZE,
    parse_video_header_v3,
    parse_video_packet_header,
)
from .video_decoder import create_h264_decoder  # noqa: E402

from pymobiledevice3.service_connection import ServiceConnection  # noqa: E402


LOGGER = logging.getLogger("xinglan.session")


async def read_exact(connection: Any, count: int, timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("手机 USB 通道超时")
        chunk = await asyncio.wait_for(connection.recv_any(count - len(data)), remaining)
        if not chunk:
            raise ConnectionError("手机 USB 通道已断开")
        data.extend(chunk)
    return bytes(data)


async def read_channel_message(
    connection: Any,
    expected_magic: bytes,
    timeout: float,
) -> tuple[MessageHeader, bytes]:
    header = unpack_header(await read_exact(connection, HEADER.size, timeout), expected_magic)
    payload = (
        await read_exact(connection, header.payload_length, timeout)
        if header.payload_length
        else b""
    )
    return header, payload


class LatestFrame:
    """只保存最新画面；新帧直接覆盖旧帧，不形成积压队列。"""

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

    def clear(self) -> None:
        """Release the retained PIL frame immediately when projection stops."""
        with self._lock:
            self._image = None
            self._sequence += 1
            self._received_at = 0.0


def prepare_xlv3_image(image: Image.Image) -> Image.Image:
    """XLV3/IOSurface supplies an upright portrait frame."""
    return image


@dataclass(frozen=True)
class SessionStats:
    status: str
    fps: float
    reconnects: int
    last_frame_age: float | None
    error: str
    control_online: bool
    status_online: bool
    status_age: float | None
    battery_percent: float | None
    phone_dropped_frames: int
    phone_control_errors: int
    decoder_backend: str
    hardware_decode: bool
    decode_errors: int


@dataclass(frozen=True)
class ControlEnvelope:
    kind: str
    value: object | None = None


class DeviceSession:
    VIDEO_PORT = 6202
    CONTROL_PORT = CONTROL_PORT
    STATUS_PORT = STATUS_PORT

    def __init__(self, udid: str, decoder_preference: str = "software") -> None:
        self.udid = udid
        self.decoder_preference = decoder_preference
        self.latest = LatestFrame()
        self._lock = threading.Lock()
        self._status = "等待连接"
        self._error = ""
        self._fps = 0.0
        self._reconnects = 0
        self._control_online = False
        self._status_online = False
        self._status_received_at = 0.0
        self._device_status: DeviceStatus | None = None
        self._decoder_backend = "等待"
        self._hardware_decode = False
        self._decode_errors = 0
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._control_queue: asyncio.Queue[ControlEnvelope] | None = None
        self._sequence = 0
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"device-{udid[-8:]}",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.latest.clear()
        with self._lock:
            self._status = "已断开投屏"
            self._error = ""
            self._fps = 0.0
            self._control_online = False
            self._status_online = False
            self._status_received_at = 0.0
            self._device_status = None
            self._decoder_backend = "等待"
            self._hardware_decode = False
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)

    def send_touch(self, kind: int, normalized_x: float, normalized_y: float) -> bool:
        try:
            phase = TouchPhase(kind)
        except ValueError:
            return False
        command = TouchCommand(
            phase=phase,
            finger=0,
            x=normalized_x,
            y=normalized_y,
            pressure=0.0 if phase in (TouchPhase.UP, TouchPhase.CANCEL) else 1.0,
            timestamp_ms=int(time.monotonic() * 1000),
        )
        return self._enqueue_control(ControlEnvelope("touch", command))

    def send_system_action(self, action: SystemAction) -> bool:
        return self._enqueue_control(ControlEnvelope("system", action))

    def send_text(self, text: str) -> bool:
        if not text or len(text.encode("utf-8")) > 1024 * 1024:
            return False
        return self._enqueue_control(ControlEnvelope("text", text))

    def send_key(self, page: int, usage: int) -> bool:
        return self._enqueue_control(ControlEnvelope("key", KeyCommand(page, usage)))

    def request_keyframe(self) -> bool:
        return self._enqueue_control(ControlEnvelope("keyframe"))

    def _enqueue_control(self, envelope: ControlEnvelope) -> bool:
        loop = self._loop
        queue = self._control_queue
        if not loop or not queue or not loop.is_running():
            return False

        def enqueue_latest() -> None:
            if queue.full():
                retained: list[ControlEnvelope] = []
                removed_move = False
                while not queue.empty():
                    queued = queue.get_nowait()
                    if (
                        not removed_move
                        and queued.kind == "touch"
                        and isinstance(queued.value, TouchCommand)
                        and queued.value.phase == TouchPhase.MOVE
                    ):
                        removed_move = True
                        continue
                    retained.append(queued)
                if not removed_move and retained:
                    retained.pop(0)
                for queued in retained[-(queue.maxsize - 1):]:
                    queue.put_nowait(queued)
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass

        loop.call_soon_threadsafe(enqueue_latest)
        return True

    def stats(self) -> SessionStats:
        sequence, received_at, _ = self.latest.snapshot()
        now = time.monotonic()
        frame_age = (now - received_at) if sequence and received_at else None
        with self._lock:
            status_age = (
                now - self._status_received_at
                if self._status_received_at
                else None
            )
            phone_status = self._device_status
            battery_percent = (
                phone_status.battery_permille / 10.0
                if phone_status is not None
                else None
            )
            return SessionStats(
                self._status,
                self._fps,
                self._reconnects,
                frame_age,
                self._error,
                self._control_online,
                self._status_online,
                status_age,
                battery_percent,
                phone_status.dropped_frames if phone_status else 0,
                phone_status.control_errors if phone_status else 0,
                self._decoder_backend,
                self._hardware_decode,
                self._decode_errors,
            )

    def _set_state(self, status: str, error: str = "") -> None:
        with self._lock:
            changed = status != self._status or error != self._error
            self._status = status
            self._error = error
        if changed:
            suffix = f" error={error}" if error else ""
            LOGGER.info("device=%s status=%s%s", self.udid, status, suffix)

    def _set_control_online(self, online: bool) -> None:
        with self._lock:
            changed = online != self._control_online
            self._control_online = online
        if changed:
            LOGGER.info("device=%s control_online=%s", self.udid, online)

    def _set_status_online(self, online: bool) -> None:
        with self._lock:
            changed = online != self._status_online
            self._status_online = online
        if changed:
            LOGGER.info("device=%s status_online=%s", self.udid, online)

    def _set_decoder_backend(self, name: str, hardware: bool) -> None:
        with self._lock:
            changed = name != self._decoder_backend or hardware != self._hardware_decode
            self._decoder_backend = name
            self._hardware_decode = hardware
        if changed:
            LOGGER.info(
                "device=%s decoder=%s hardware=%s", self.udid, name, hardware
            )

    def _publish_device_status(self, status: DeviceStatus) -> None:
        with self._lock:
            self._device_status = status
            self._status_received_at = time.monotonic()
            self._status_online = True

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        if self._sequence == 0:
            self._sequence = 1
        return self._sequence

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._control_queue = asyncio.Queue(maxsize=64)
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
        tasks = [
            asyncio.create_task(self._video_supervisor()),
            asyncio.create_task(self._control_supervisor()),
            asyncio.create_task(self._status_supervisor()),
        ]
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.2)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _open_channel(self, port: int, timeout: float = 4.0) -> Any:
        return await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                self.udid,
                port,
                connection_type="USB",
            ),
            timeout=timeout,
        )

    @staticmethod
    async def _close_channel(connection: Any | None) -> None:
        if connection is None:
            return
        try:
            await connection.close()
        except Exception:
            pass

    async def _video_supervisor(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            connection = None
            try:
                self._set_state("正在连接视频")
                connection = await self._open_channel(self.VIDEO_PORT, 3.0)
                delay = 0.5
                self.request_keyframe()
                await self._receive_video(connection)
                raise ConnectionError("视频连接已结束")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._reconnects += 1
                self.request_keyframe()
                self._set_state("视频重连中", str(exc))
                await asyncio.sleep(delay)
                delay = min(5.0, delay * 1.6)
            finally:
                await self._close_channel(connection)

    async def _receive_video(self, connection: Any) -> None:
        magic = await read_exact(connection, 4, 5.0)
        if magic != VIDEO_MAGIC_V3:
            raise ValueError(f"手机未运行星澜 XLV3 投屏服务：{magic!r}")
        header_bytes = magic + await read_exact(connection, VIDEO_HEADER_V3_SIZE - 4, 5.0)
        header = parse_video_header_v3(header_bytes)
        decoder = create_h264_decoder(self.decoder_preference)
        self._set_decoder_backend(decoder.name, decoder.hardware)
        self._set_state(
            f"投屏中 {header.width}×{header.height} · {decoder.name} · {self.VIDEO_PORT}"
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
            try:
                decoded = decoder.decode(payload)
            except Exception as exc:
                with self._lock:
                    self._decode_errors += 1
                if decoder.hardware:
                    LOGGER.warning(
                        "device=%s hardware decode failed, falling back: %s",
                        self.udid,
                        exc,
                    )
                    decoder = create_h264_decoder("software")
                    self._set_decoder_backend(decoder.name, decoder.hardware)
                    self.request_keyframe()
                    continue
                raise
            for frame in decoded:
                self.latest.publish(prepare_xlv3_image(frame.to_image()))
                frame_count += 1
            now = time.monotonic()
            elapsed = now - window_started
            if elapsed >= 2.0:
                with self._lock:
                    self._fps = frame_count / elapsed
                frame_count = 0
                window_started = now

    async def _control_handshake(self, connection: Any) -> None:
        sequence = self._next_sequence()
        await asyncio.wait_for(
            connection.sendall(pack_hello(CONTROL_MAGIC, sequence)),
            timeout=1.0,
        )
        header, payload = await read_channel_message(connection, CONTROL_MAGIC, 2.0)
        if header.message_type != MessageType.HELLO_ACK or header.sequence != sequence:
            raise ValueError("控制通道握手响应不匹配")
        unpack_hello(payload)

    async def _send_control_envelope(self, connection: Any, envelope: ControlEnvelope) -> None:
        sequence = self._next_sequence()
        if envelope.kind == "touch" and isinstance(envelope.value, TouchCommand):
            packet = pack_touch(envelope.value, sequence)
        elif envelope.kind == "system" and isinstance(envelope.value, SystemAction):
            packet = pack_system_action(envelope.value, sequence)
        elif envelope.kind == "text" and isinstance(envelope.value, str):
            packet = pack_text_input(envelope.value, sequence)
        elif envelope.kind == "key" and isinstance(envelope.value, KeyCommand):
            packet = pack_key_event(envelope.value, sequence)
        elif envelope.kind == "keyframe":
            packet = pack_keyframe_request(sequence)
        else:
            raise ValueError(f"未知控制命令：{envelope.kind}")
        await asyncio.wait_for(connection.sendall(packet), timeout=1.0)
        header, payload = await read_channel_message(connection, CONTROL_MAGIC, 1.5)
        if header.message_type != MessageType.ACK:
            raise ValueError(f"控制命令未收到 ACK：{header.message_type.name}")
        acknowledgement = unpack_ack(payload)
        if acknowledgement.acknowledged_sequence != sequence:
            raise ValueError("控制 ACK 序列号不匹配")
        if acknowledgement.result_code != 0:
            raise RuntimeError(f"手机拒绝控制命令，代码 {acknowledgement.result_code}")

    async def _send_control_ping(self, connection: Any) -> None:
        sequence = self._next_sequence()
        packet = pack_ping(
            CONTROL_MAGIC,
            sequence,
            int(time.monotonic() * 1000),
        )
        await asyncio.wait_for(connection.sendall(packet), timeout=1.0)
        header, _ = await read_channel_message(connection, CONTROL_MAGIC, 1.5)
        if header.message_type != MessageType.PONG or header.sequence != sequence:
            raise ValueError("控制心跳响应不匹配")

    async def _control_supervisor(self) -> None:
        assert self._control_queue is not None
        pending: ControlEnvelope | None = None
        delay = 0.3
        while not self._stop.is_set():
            connection = None
            try:
                connection = await self._open_channel(self.CONTROL_PORT)
                await self._control_handshake(connection)
                self._set_control_online(True)
                delay = 0.3
                await self._send_control_envelope(connection, ControlEnvelope("keyframe"))
                while not self._stop.is_set():
                    if pending is None:
                        try:
                            pending = await asyncio.wait_for(
                                self._control_queue.get(),
                                timeout=1.5,
                            )
                        except asyncio.TimeoutError:
                            await self._send_control_ping(connection)
                            continue
                    await self._send_control_envelope(connection, pending)
                    pending = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_control_online(False)
                LOGGER.warning("device=%s control reconnect: %s", self.udid, exc)
                await asyncio.sleep(delay)
                delay = min(3.0, delay * 1.5)
            finally:
                self._set_control_online(False)
                await self._close_channel(connection)

    async def _status_supervisor(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            connection = None
            try:
                connection = await self._open_channel(self.STATUS_PORT)
                delay = 0.5
                while not self._stop.is_set():
                    header, payload = await read_channel_message(connection, STATUS_MAGIC, 3.5)
                    if header.message_type == MessageType.HELLO:
                        unpack_hello(payload)
                        self._set_status_online(True)
                    elif header.message_type == MessageType.DEVICE_STATUS:
                        self._publish_device_status(unpack_device_status(payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_status_online(False)
                LOGGER.warning("device=%s status reconnect: %s", self.udid, exc)
                await asyncio.sleep(delay)
                delay = min(5.0, delay * 1.5)
            finally:
                self._set_status_online(False)
                await self._close_channel(connection)
