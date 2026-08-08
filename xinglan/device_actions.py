from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any

from .bootstrap import configure_dependencies
from .control_protocol import (
    CONTROL_MAGIC,
    CONTROL_PORT,
    HEADER,
    MessageType,
    SystemAction,
    pack_hello,
    pack_system_action,
    unpack_ack,
    unpack_header,
    unpack_hello,
)


configure_dependencies()

from pymobiledevice3.service_connection import ServiceConnection  # noqa: E402


ACTION_MAP = {
    "home": SystemAction.HOME,
    "wake": SystemAction.WAKE,
    "sleep": SystemAction.LOCK,
    "switch": SystemAction.APP_SWITCHER,
}


async def _read_exact(connection: Any, count: int, timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("手机控制通道超时")
        chunk = await asyncio.wait_for(connection.recv_any(count - len(data)), remaining)
        if not chunk:
            raise ConnectionError("手机控制通道已断开")
        data.extend(chunk)
    return bytes(data)


async def _read_message(
    connection: Any,
    timeout: float,
) -> tuple[Any, bytes]:
    header = unpack_header(
        await _read_exact(connection, HEADER.size, timeout),
        CONTROL_MAGIC,
    )
    payload = (
        await _read_exact(connection, header.payload_length, timeout)
        if header.payload_length
        else b""
    )
    return header, payload


async def send_device_action(udid: str, action: str, timeout: float = 3.0) -> bool:
    system_action = ACTION_MAP.get(action)
    if system_action is None:
        raise ValueError(f"不支持的手机动作：{action}")

    connection: Any | None = None
    try:
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                udid,
                CONTROL_PORT,
                connection_type="USB",
            ),
            timeout=timeout,
        )

        hello_sequence = 1
        await asyncio.wait_for(
            connection.sendall(pack_hello(CONTROL_MAGIC, hello_sequence)),
            timeout=1.0,
        )
        hello_header, hello_payload = await _read_message(connection, timeout)
        if (
            hello_header.message_type != MessageType.HELLO_ACK
            or hello_header.sequence != hello_sequence
        ):
            return False
        unpack_hello(hello_payload)

        action_sequence = 2
        await asyncio.wait_for(
            connection.sendall(pack_system_action(system_action, action_sequence)),
            timeout=1.0,
        )
        action_header, action_payload = await _read_message(connection, timeout)
        if action_header.message_type != MessageType.ACK:
            return False
        acknowledgement = unpack_ack(action_payload)
        return (
            acknowledgement.acknowledged_sequence == action_sequence
            and acknowledgement.result_code == 0
        )
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def send_action_to_devices(
    udids: Iterable[str], action: str
) -> dict[str, bool]:
    ordered = list(dict.fromkeys(udids))
    # The action is deliberately broadcast-like: all USB phones should
    # receive the wake/lock command in the same moment.  A small semaphore
    # made 60 phones run in five visible batches, which looked like one-by-one
    # screen changes.  Keep only a light safety cap above the supported 60
    # devices so usbmux can perform the handshakes concurrently.
    semaphore = asyncio.Semaphore(64)

    async def send_one(udid: str) -> bool:
        async with semaphore:
            return await send_device_action(udid, action)

    results = await asyncio.gather(
        *(send_one(udid) for udid in ordered),
        return_exceptions=True,
    )
    return {
        udid: bool(result) if not isinstance(result, BaseException) else False
        for udid, result in zip(ordered, results)
    }


async def identify_physical_device(udid: str) -> bool:
    """Turn one phone off and back on through the independent XLStream service."""
    slept = await send_device_action(udid, "sleep")
    await asyncio.sleep(0.6)
    woke = await send_device_action(udid, "wake")
    return slept and woke
