from __future__ import annotations

import enum
import struct
from dataclasses import dataclass


CONTROL_PORT = 6203
STATUS_PORT = 6204
FILE_PORT = 6205
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
    TEXT_INPUT = 13
    KEY_EVENT = 14
    # High-frequency absolute touch positions. The phone intentionally does
    # not ACK this message, so stale MOVE packets cannot pile up behind USB
    # round trips. DOWN/UP/CANCEL remain acknowledged TOUCH messages.
    TOUCH_STREAM = 15
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


CAPABILITY_TOUCH_STREAM = 1 << 7


@dataclass(frozen=True)
class KeyCommand:
    page: int
    usage: int
    modifiers: int = 0


class KeyModifier(enum.IntFlag):
    CONTROL = 1 << 0
    SHIFT = 1 << 1
    ALT = 1 << 2
    GUI = 1 << 3


class SystemAction(enum.IntEnum):
    HOME = 1
    WAKE = 2
    LOCK = 3
    SCREENSHOT = 4
    APP_SWITCHER = 5
    CONTROL_CENTER = 6


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


@dataclass(frozen=True)
class Hello:
    capabilities: int
    screen_width: int
    screen_height: int
    protocol_version: int


@dataclass(frozen=True)
class Acknowledgement:
    acknowledged_sequence: int
    result_code: int


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


def pack_hello(
    magic: bytes,
    sequence: int,
    capabilities: int = 0,
    screen_width: int = 0,
    screen_height: int = 0,
) -> bytes:
    payload = HELLO.pack(
        capabilities & 0xFFFFFFFF,
        screen_width & 0xFFFF,
        screen_height & 0xFFFF,
        PROTOCOL_VERSION,
        0,
    )
    return pack_message(magic, MessageType.HELLO, sequence, payload)


def unpack_hello(payload: bytes) -> Hello:
    if len(payload) != HELLO.size:
        raise ValueError(f"invalid hello payload length: {len(payload)}")
    capabilities, width, height, version, _ = HELLO.unpack(payload)
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported peer protocol version: {version}")
    return Hello(capabilities, width, height, version)


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


def pack_touch_stream(command: TouchCommand, sequence: int) -> bytes:
    if command.phase != TouchPhase.MOVE:
        raise ValueError("touch stream only accepts MOVE")
    payload = TOUCH.pack(
        int(command.phase),
        command.finger & 0xFF,
        _unit_to_u16(command.x),
        _unit_to_u16(command.y),
        _unit_to_u16(command.pressure),
        command.timestamp_ms & 0xFFFFFFFF,
    )
    return pack_message(CONTROL_MAGIC, MessageType.TOUCH_STREAM, sequence, payload)


def pack_system_action(action: SystemAction, sequence: int) -> bytes:
    return pack_message(
        CONTROL_MAGIC,
        MessageType.SYSTEM_ACTION,
        sequence,
        SYSTEM_ACTION.pack(int(action), 0),
    )


def pack_keyframe_request(sequence: int) -> bytes:
    return pack_message(CONTROL_MAGIC, MessageType.REQUEST_KEYFRAME, sequence)


def pack_text_input(text: str, sequence: int) -> bytes:
    payload = text.encode("utf-8")
    if not payload or len(payload) > MAX_PAYLOAD:
        raise ValueError("text input is empty or too large")
    return pack_message(CONTROL_MAGIC, MessageType.TEXT_INPUT, sequence, payload)


def pack_key_event(command: KeyCommand, sequence: int) -> bytes:
    if not 0 <= command.page <= 0xFFFFFFFF or not 0 <= command.usage <= 0xFFFFFFFF:
        raise ValueError("invalid HID key usage")
    if not 0 <= command.modifiers <= 0x0F:
        raise ValueError("invalid HID key modifiers")
    payload = struct.pack("!II", command.page, command.usage)
    return pack_message(
        CONTROL_MAGIC,
        MessageType.KEY_EVENT,
        sequence,
        payload,
        flags=command.modifiers,
    )


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


def unpack_ack(payload: bytes) -> Acknowledgement:
    if len(payload) != ACK.size:
        raise ValueError(f"invalid acknowledgement length: {len(payload)}")
    acknowledged_sequence, result_code = ACK.unpack(payload)
    return Acknowledgement(acknowledged_sequence, result_code)


def unpack_device_status(payload: bytes) -> DeviceStatus:
    if len(payload) != DEVICE_STATUS.size:
        raise ValueError(f"invalid device status length: {len(payload)}")
    uptime, battery, flags, fps_x10, clients, dropped, errors = DEVICE_STATUS.unpack(payload)
    return DeviceStatus(uptime, battery, flags, fps_x10 / 10.0, clients, dropped, errors)
