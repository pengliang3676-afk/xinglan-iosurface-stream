from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Final


LOGGER = logging.getLogger("xinglan.ioscpy_touch")

MAGIC: Final[int] = 0x49435059  # ICPY
PROTOCOL_VERSION: Final[int] = 4
HEADER_SIZE: Final[int] = 32
# Formal XLStream owns a private port.  Keep it separate from an independently
# installed upstream ioscpy package so upgrades can be tested without either
# daemon stealing the other's listener.
DEVICE_PORT: Final[int] = 27185
MSG_HELLO: Final[int] = 1
MSG_HELLO_ACK: Final[int] = 2
MSG_AUTHENTICATE: Final[int] = 5
MSG_INPUT_TOUCH: Final[int] = 20
CHANNEL_CONTROL: Final[int] = 0

# XLStream: 1=down, 2=move, 0=up. ioscpy: 0=down, 1=move, 2=up.
XL_TO_IOSCPY_PHASE: Final[dict[int, int]] = {1: 0, 2: 1, 0: 2}


class IoscpyTouchError(RuntimeError):
    pass


def encode_frame(msg_type: int, seq: int, payload: bytes) -> bytes:
    header = struct.pack(
        ">IHHIQQI",
        MAGIC,
        PROTOCOL_VERSION,
        msg_type,
        0,
        CHANNEL_CONTROL,
        seq,
        len(payload),
    )
    assert len(header) == HEADER_SIZE
    return header + payload


def read_frame(stream: socket.socket) -> tuple[int, int, bytes]:
    header = _recv_exact(stream, HEADER_SIZE)
    magic, version, msg_type, _flags, _stream_id, seq, length = struct.unpack(
        ">IHHIQQI", header
    )
    if magic != MAGIC:
        raise IoscpyTouchError(f"ioscpy magic错误: {magic:#x}")
    if version != PROTOCOL_VERSION:
        raise IoscpyTouchError(f"ioscpy协议版本不匹配: {version}")
    if length > 16 * 1024 * 1024:
        raise IoscpyTouchError(f"ioscpy消息过大: {length}")
    return msg_type, seq, _recv_exact(stream, length)


def encode_touch(kind: int, x: float, y: float, finger: int = 0) -> bytes:
    try:
        phase = XL_TO_IOSCPY_PHASE[kind]
    except KeyError as exc:
        raise ValueError(f"未知XLStream触控阶段: {kind}") from exc
    return struct.pack(">BBff", phase, finger, _unit(x), _unit(y))


def fit_image_bounds(
    canvas_width: int,
    canvas_height: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Return centered contain bounds without distorting the phone picture."""
    if min(canvas_width, canvas_height, image_width, image_height) <= 0:
        return 0, 0, max(1, canvas_width), max(1, canvas_height)
    scale = min(canvas_width / image_width, canvas_height / image_height)
    width = max(1, round(image_width * scale))
    height = max(1, round(image_height * scale))
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    return left, top, left + width, top + height


def normalize_canvas_point(
    x: float,
    y: float,
    bounds: tuple[int, int, int, int],
    *,
    clamp: bool = False,
) -> tuple[float, float] | None:
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    if not clamp and (x < left or x >= right or y < top or y >= bottom):
        return None
    return _unit((x - left) / (right - left)), _unit((y - top) / (bottom - top))


class IoscpyTouchClient:
    """One-device FIFO touch channel using ioscpy's native touch lifecycle."""

    def __init__(self, udid: str, iproxy_path: Path) -> None:
        self.udid = udid
        self.iproxy_path = iproxy_path
        self._commands: queue.Queue[bytes | None] = queue.Queue(maxsize=2048)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = ""
        self._seq = 0
        self._last_point: tuple[float, float] | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"ioscpy-touch-{udid[-8:]}",
            daemon=True,
        )

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self._stop.is_set()

    @property
    def error(self) -> str:
        return self._error

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._commands.put_nowait(None)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def send_touch(self, kind: int, x: float, y: float) -> bool:
        if not self.ready:
            return False
        point = (_unit(x), _unit(y))
        if kind == 2 and self._last_point is not None:
            if (
                abs(point[0] - self._last_point[0]) <= 0.001
                and abs(point[1] - self._last_point[1]) <= 0.001
            ):
                return True
        payload = encode_touch(kind, x, y)
        try:
            # Keep the exact mouse path. Never collapse it into a fixed swipe.
            self._commands.put_nowait(payload)
            self._last_point = None if kind == 0 else point
            return True
        except queue.Full:
            self._error = "ioscpy触控队列已满"
            return False

    def _run(self) -> None:
        process: subprocess.Popen[bytes] | None = None
        stream: socket.socket | None = None
        try:
            local_port = _free_local_port()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                iproxy_command(self.iproxy_path, local_port, self.udid),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            stream = _connect_forward(local_port, process, self._stop)
            stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            stream.settimeout(8.0)
            self._handshake(stream)
            stream.settimeout(None)
            self._ready.set()
            LOGGER.info("ioscpy touch ready: %s", self.udid)

            while not self._stop.is_set():
                try:
                    payload = self._commands.get(timeout=0.25)
                except queue.Empty:
                    continue
                if payload is None:
                    break
                self._seq += 1
                stream.sendall(encode_frame(MSG_INPUT_TOUCH, self._seq, payload))
        except Exception as exc:
            self._error = str(exc)
            LOGGER.exception("ioscpy touch failed: %s", self.udid)
        finally:
            self._ready.clear()
            if stream is not None:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _handshake(self, stream: socket.socket) -> None:
        hello = json.dumps(
            {
                "role": "host",
                "host_version": "xlstream-ioscpy-1",
                "protocol_version": PROTOCOL_VERSION,
                "nonce": secrets.token_hex(16),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        stream.sendall(encode_frame(MSG_HELLO, 0, hello))
        msg_type, _seq, payload = read_frame(stream)
        if msg_type != MSG_HELLO_ACK:
            raise IoscpyTouchError(f"ioscpy握手失败，消息类型={msg_type}")
        ack = json.loads(payload.decode("utf-8"))
        if int(ack.get("protocol_version", -1)) != PROTOCOL_VERSION:
            raise IoscpyTouchError("ioscpy手机端协议版本不匹配")
        token = str(ack.get("session_token", "")).encode("utf-8")
        if not token:
            raise IoscpyTouchError("ioscpy手机端未返回会话令牌")
        stream.sendall(encode_frame(MSG_AUTHENTICATE, 0, token))


class IoscpyTouchManager:
    """Own all active ioscpy touch channels and release them with each group."""

    def __init__(self, project_dir: Path) -> None:
        self.enabled = os.environ.get("XL_TOUCH_BACKEND", "ioscpy").lower() == "ioscpy"
        self.iproxy_path = project_dir.parent / "independent-usbmux-test" / "iproxy.exe"
        self._clients: dict[str, IoscpyTouchClient] = {}
        # Optional safety lock used only by the first single-phone acceptance run.
        self._allowed_udid = os.environ.get("XL_TOUCH_SINGLE_UDID", "").strip()
        if self.enabled and not self.iproxy_path.is_file():
            raise IoscpyTouchError(f"找不到iproxy: {self.iproxy_path}")

    def _allowed(self, udid: str) -> bool:
        return not self._allowed_udid or udid == self._allowed_udid

    def start(self, udid: str) -> None:
        if not self.enabled or not self._allowed(udid):
            return
        client = self._clients.get(udid)
        # Python threads are one-shot; replace a failed channel on reconnect.
        if client is not None and not client.ready and client.error:
            client.stop()
            client = None
        if client is None:
            client = IoscpyTouchClient(udid, self.iproxy_path)
            self._clients[udid] = client
        client.start()

    def stop(self, udid: str) -> None:
        client = self._clients.pop(udid, None)
        if client is not None:
            client.stop()

    def close(self) -> None:
        for udid in list(self._clients):
            self.stop(udid)

    def send_touch(self, udid: str, kind: int, x: float, y: float) -> bool:
        if not self.enabled or not self._allowed(udid):
            return False
        client = self._clients.get(udid)
        return bool(client and client.send_touch(kind, x, y))

    def status(self, udid: str) -> str:
        if not self._allowed(udid):
            return "单机验证锁定"
        client = self._clients.get(udid)
        if client is None:
            return "未启动"
        if client.ready:
            return "已连接"
        return client.error or "正在连接"


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def iproxy_command(iproxy_path: Path, local_port: int, udid: str) -> list[str]:
    return [
        str(iproxy_path),
        str(local_port),
        str(DEVICE_PORT),
        "-u",
        udid,
        "-l",
    ]


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    parts = bytearray()
    while len(parts) < size:
        chunk = stream.recv(size - len(parts))
        if not chunk:
            raise IoscpyTouchError("ioscpy连接提前关闭")
        parts.extend(chunk)
    return bytes(parts)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _connect_forward(
    local_port: int,
    process: subprocess.Popen[bytes],
    stop: threading.Event,
) -> socket.socket:
    deadline = time.monotonic() + 6.0
    last_error: OSError | None = None
    while not stop.is_set() and time.monotonic() < deadline:
        if process.poll() is not None:
            raise IoscpyTouchError(f"iproxy提前退出: {process.returncode}")
        try:
            return socket.create_connection(("127.0.0.1", local_port), timeout=0.5)
        except OSError as exc:
            last_error = exc
            time.sleep(0.08)
    raise IoscpyTouchError(f"无法建立ioscpy USB通道: {last_error}")
