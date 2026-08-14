from __future__ import annotations

import struct
from dataclasses import dataclass


VIDEO_MAGIC = b"ZXH2"
VIDEO_MAGIC_V3 = b"XLV3"
VIDEO_HEADER_SIZE = 12
VIDEO_HEADER_V3_SIZE = 16
VIDEO_PACKET_HEADER_SIZE = 16
MAX_ENCODED_FRAME = 4 * 1024 * 1024


@dataclass(frozen=True)
class VideoHeader:
    protocol: str
    width: int
    height: int
    fps: int


@dataclass(frozen=True)
class VideoPacketHeader:
    packet_type: int
    flags: int
    payload_length: int
    sequence: int
    timestamp_ms: int


def parse_video_header(data: bytes) -> VideoHeader:
    if len(data) != VIDEO_HEADER_SIZE:
        raise ValueError(f"视频握手长度错误：{len(data)}")
    if data[:4] != VIDEO_MAGIC:
        raise ValueError("手机端视频协议不匹配")
    width, height, fps = struct.unpack("!HHH", data[4:10])
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and 1 <= fps <= 120):
        raise ValueError(f"无效视频参数：{width}x{height} @{fps}")
    return VideoHeader(protocol="ZXH2", width=width, height=height, fps=fps)


def parse_video_header_v3(data: bytes) -> VideoHeader:
    if len(data) != VIDEO_HEADER_V3_SIZE:
        raise ValueError(f"XLV3视频握手长度错误：{len(data)}")
    if data[:4] != VIDEO_MAGIC_V3:
        raise ValueError("手机端XLV3视频协议不匹配")
    width, height, fps = struct.unpack("!HHH", data[4:10])
    codec = data[10]
    if codec != 1:
        raise ValueError(f"暂不支持的视频编码：{codec}")
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and 1 <= fps <= 120):
        raise ValueError(f"无效XLV3视频参数：{width}x{height} @{fps}")
    return VideoHeader(protocol="XLV3", width=width, height=height, fps=fps)


def parse_video_packet_header(data: bytes) -> VideoPacketHeader:
    if len(data) != VIDEO_PACKET_HEADER_SIZE:
        raise ValueError(f"XLV3分包头长度错误：{len(data)}")
    packet_type, flags, _reserved, payload_length, sequence, timestamp_ms = struct.unpack(
        "!BBHIII", data
    )
    if packet_type not in (1, 2, 3):
        raise ValueError(f"未知XLV3分包类型：{packet_type}")
    if payload_length > MAX_ENCODED_FRAME:
        raise ValueError(f"XLV3负载过大：{payload_length}")
    if packet_type == 1 and payload_length == 0:
        raise ValueError("XLV3视频分包为空")
    return VideoPacketHeader(packet_type, flags, payload_length, sequence, timestamp_ms)


def parse_frame_size(data: bytes) -> int:
    if len(data) != 4:
        raise ValueError("视频帧长度头不完整")
    value = struct.unpack("!I", data)[0]
    if value <= 0 or value > MAX_ENCODED_FRAME:
        raise ValueError(f"异常视频帧长度：{value}")
    return value
