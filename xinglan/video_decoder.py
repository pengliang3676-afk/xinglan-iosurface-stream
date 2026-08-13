from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .bootstrap import configure_dependencies


LOGGER = logging.getLogger("xinglan.video_decoder")


@dataclass
class H264Decoder:
    context: Any | None
    name: str

    def decode(self, payload: bytes) -> list[Any]:
        import av

        if self.context is None:
            return []
        return self.context.decode(av.Packet(payload))

    def close(self) -> None:
        """Release the FFmpeg codec context and its native worker resources."""
        context = self.context
        self.context = None
        if context is None:
            return
        try:
            context.flush_buffers()
        except Exception:
            pass


def create_h264_decoder() -> H264Decoder:
    """Create the production low-latency software H.264 decoder."""
    configure_dependencies()
    import av

    context = av.CodecContext.create("h264", "r")
    # 十台设备各自只保留最新帧；限制每个软解码器线程数，避免线程过量争抢。
    context.thread_count = 1
    return H264Decoder(context, "软件")
