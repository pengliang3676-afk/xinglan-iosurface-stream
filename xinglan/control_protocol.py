from __future__ import annotations

import enum
import struct
from dataclasses import dataclass


CONTROL_PORT = 6203
STATUS_PORT = 6204
PROTOCOL_VERSION = 1
CONTROL_MAGIC = b"XLC1"
STATUS_MAGIC = b"XLS1"
MAX_PAYLOAD = 1024 * 1024

HEADER = struct.Struct("!4sBBHII")
HELLO = struct.Struct("!IHHHH")
TOUCH = struct.Struct("!BBHHHI")
SYSTEM_ACTION = struct.Struct("!HH")
PING = struct.Struct("!Q")
ACK = struct.Struct("!II")
DEVICE_STATUS = struct.Struct("!IHHHHII")


class MessageType(enum.IntEnum):
    HELLO = 1
    HELLO_ACK = 2
    TOUCH = 10
    SYSTEM_ACTION = 11
    REQUEST_KEYFRAME = 12
    PING = 20
    PONG = 21
    ACK = 22
    ERROR = 23
    DEVICE_STATUS = 30
    VIDEO_STATS = 31


class TouchPhase(enum.IntEnum):
    UP = 0
    DOWN = 1
    MOVE = 2
    CANCEL = 3


class SystemAction(enum.IntEnum):
    HOME = 1
    WAKE = 2
    LOCK = 3
    SCREENSHOT = 4


@dataclass(frozen=True)
class MessageHeader:
    magic: bytes
    version: int
    message_type: MessageType
    flags: int
    payload_length: int
    sequence: int


@dataclass(frozen=True)
class TouchCommand:
    phase: TouchPhase
    finger: int
    x: float
    y: float
    pressure: float
    timestamp_ms: int


@dataclass(frozen=True)
class DeviceStatus:
    uptime_seconds: int
    battery_permille: int
    flags: int
    video_fps: float
    video_clients: int
    dropped_frames: int
    control_errors: int


def pack_header(
    magic: bytes,
    message_type: MessageType,
    payload_length: int,
    sequence: int,
    flags: int = 0,
) -> bytes:
    if magic not in (CONTROL_MAGIC, STATUS_MAGIC):
        raise ValueError("invalid channel magic")
    if not 0 <= payload_length <= MAX_PAYLOAD:
        raise ValueError("invalid payload length")
    return HEADER.pack(
        magic,
        PROTOCOL_VERSION,
        int(message_type),
        flags & 0xFFFF,
        payload_length,
        sequence & 0xFFFFFFFF,
    )


def unpack_header(data: bytes, expected_magic: bytes | None = None) -> MessageHeader:
    if len(data) != HEADER.size:
        raise ValueError(f"invalid header length: {len(data)}")
    magic, version, raw_type, flags, payload_length, sequence = HEADER.unpack(data)
    if expected_magic is not None and magic != expected_magic:
        raise ValueError(f"unexpected channel magic: {magic!r}")
    if magic not in (CONTROL_MAGIC, STATUS_MAGIC):
        raise ValueError(f"unknown channel magic: {magic!r}")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version: {version}")
    if payload_length > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {payload_length}")
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise ValueError(f"unknown message type: {raw_type}") from exc
    return MessageHeader(magic, version, message_type, flags, payload_length, sequence)


def pack_message(
    magic: bytes,
    message_type: MessageType,
    sequence: int,
    payload: bytes = b"",
    flags: int = 0,
) -> bytes:
    return pack_header(magic, message_type, len(payload), sequence, flags) + payload


def _unit_to_u16(value: float) -> int:
    return round(max(0.0, min(1.0, value)) * 65535.0)


def pack_touch(command: TouchCommand, sequence: int) -> bytes:
    payload = TOUCH.pack(
        int(command.phase),
        command.finger & 0xFF,
        _unit_to_u16(command.x),
        _unit_to_u16(command.y),
        _unit_to_u16(command.pressure),
        command.timestamp_ms & 0xFFFFFFFF,
    )
    return pack_message(CONTROL_MAGIC, MessageType.TOUCH, sequence, payload)


def unpack_touch(payload: bytes) -> TouchCommand:
    if len(payload) != TOUCH.size:
        raise ValueError(f"invalid touch payload length: {len(payload)}")
    phase, finger, x, y, pressure, timestamp_ms = TOUCH.unpack(payload)
    return TouchCommand(
        TouchPhase(phase),
        finger,
        x / 65535.0,
        y / 65535.0,
        pressure / 65535.0,
        timestamp_ms,
    )


def pack_ping(magic: bytes, sequence: int, monotonic_ms: int, pong: bool = False) -> bytes:
    message_type = MessageType.PONG if pong else MessageType.PING
    return pack_message(magic, message_type, sequence, PING.pack(monotonic_ms & 0xFFFFFFFFFFFFFFFF))


def unpack_device_status(payload: bytes) -> DeviceStatus:
    if len(payload) != DEVICE_STATUS.size:
        raise ValueError(f"invalid device status length: {len(payload)}")
    uptime, battery, flags, fps_x10, clients, dropped, errors = DEVICE_STATUS.unpack(payload)
    return DeviceStatus(uptime, battery, flags, fps_x10 / 10.0, clients, dropped, errors)
