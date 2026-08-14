from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from typing import Any

from .bootstrap import configure_dependencies
from .control_protocol import (
    CONTROL_MAGIC,
    CONTROL_PORT,
    HEADER,
    MessageType,
    SystemAction,
    pack_hello,
    pack_ping,
    pack_system_action,
    unpack_ack,
    unpack_header,
    unpack_hello,
)


configure_dependencies()

from .usb_connection import ServiceConnection  # noqa: E402


ACTION_MAP = {
    "home": SystemAction.HOME,
    "wake": SystemAction.WAKE,
    "sleep": SystemAction.LOCK,
    "switch": SystemAction.APP_SWITCHER,
}

# Exact command channel used by the legacy browser version's two top buttons.
LEGACY_CONTROL_PORT = 6000
LEGACY_COMMAND_BYTES = {
    "wake": b"14\r\n",
    "sleep": b"15\r\n",
}
ON_DEMAND_MAX_CONNECTIONS = 10


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


async def send_device_action_sequence(
    udid: str,
    actions: Iterable[str],
    timeout: float = 3.0,
    should_continue: Callable[[], bool] | None = None,
) -> bool:
    system_actions: list[SystemAction] = []
    for action in actions:
        system_action = ACTION_MAP.get(action)
        if system_action is None:
            raise ValueError(f"不支持的手机动作：{action}")
        system_actions.append(system_action)
    if not system_actions:
        return True
    if should_continue is not None and not should_continue():
        return False

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

        for action_sequence, system_action in enumerate(system_actions, start=2):
            # A later power command (especially SLEEP) invalidates an older
            # WAKE/HOME sequence.  Never let its delayed HOME overtake sleep.
            if should_continue is not None and not should_continue():
                return False
            await asyncio.wait_for(
                connection.sendall(
                    pack_system_action(system_action, action_sequence)
                ),
                timeout=1.0,
            )
            action_header, action_payload = await _read_message(connection, timeout)
            if action_header.message_type != MessageType.ACK:
                return False
            acknowledgement = unpack_ack(action_payload)
            if (
                acknowledgement.acknowledged_sequence != action_sequence
                or acknowledgement.result_code != 0
            ):
                return False
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def send_device_action(udid: str, action: str, timeout: float = 3.0) -> bool:
    return await send_device_action_sequence(udid, [action], timeout=timeout)


async def send_action_to_devices(
    udids: Iterable[str], action: str
) -> dict[str, bool]:
    ordered = list(dict.fromkeys(udids))
    # Port 6203 is opened only while a command is being delivered.  Keep the
    # usbmux handshake burst bounded so a 60-phone button press cannot create
    # sixty Apple driver stacks at exactly the same instant.
    semaphore = asyncio.Semaphore(ON_DEMAND_MAX_CONNECTIONS)

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


async def send_action_sequence_to_devices(
    udids: Iterable[str],
    actions: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    ordered = list(dict.fromkeys(udids))
    sequence = list(actions)
    semaphore = asyncio.Semaphore(ON_DEMAND_MAX_CONNECTIONS)

    async def send_one(udid: str) -> bool:
        async with semaphore:
            if should_continue is None:
                return await send_device_action_sequence(udid, sequence)
            return await send_device_action_sequence(
                udid,
                sequence,
                should_continue=should_continue,
            )

    results = await asyncio.gather(
        *(send_one(udid) for udid in ordered),
        return_exceptions=True,
    )
    return {
        udid: bool(result) if not isinstance(result, BaseException) else False
        for udid, result in zip(ordered, results)
    }


async def send_legacy_device_action(
    udid: str,
    action: str,
    timeout: float = 2.5,
    should_continue: Callable[[], bool] | None = None,
) -> bool:
    """Send the same fire-and-close command as the legacy browser backend."""
    payload = LEGACY_COMMAND_BYTES.get(action)
    if payload is None:
        raise ValueError(f"不支持的旧版手机动作：{action}")

    connection: Any | None = None
    try:
        if should_continue is not None and not should_continue():
            return False
        connection = await asyncio.wait_for(
            ServiceConnection.create_using_usbmux(
                udid,
                LEGACY_CONTROL_PORT,
                connection_type="USB",
            ),
            timeout=timeout,
        )
        if should_continue is not None and not should_continue():
            return False
        await asyncio.wait_for(connection.sendall(payload), timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass


async def send_legacy_action_to_devices(
    udids: Iterable[str],
    action: str,
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    """Use port 6000 only for the duration of this one button command."""
    ordered = list(dict.fromkeys(udids))
    semaphore = asyncio.Semaphore(ON_DEMAND_MAX_CONNECTIONS)

    async def send_one(udid: str) -> bool:
        async with semaphore:
            return await send_legacy_device_action(
                udid,
                action,
                should_continue=should_continue,
            )

    results = await asyncio.gather(
        *(send_one(udid) for udid in ordered),
        return_exceptions=True,
    )
    return {
        udid: bool(result) if not isinstance(result, BaseException) else False
        for udid, result in zip(ordered, results)
    }


async def send_reliable_wake_to_devices(
    udids: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    """Wake instantly, verify the display, then return every phone home.

    The legacy ``14`` command is fire-and-forget: a successful USB write does
    not prove that the display actually woke.  The binary XLStream action waits
    for the phone-side handler to check the display and apply its power/home
    fallback.  The explicit HOME action is still required because a successful
    wake notification returns as soon as the display is on.

    Plug-ins without the binary control channel retain the legacy result, so
    the top button stays backwards compatible.
    """
    ordered = list(dict.fromkeys(udids))
    # Current phones use the acknowledged 6203 channel.  Open the legacy 6000
    # listener only for phones that did not accept 6203, then close it
    # immediately.  This preserves old plug-in compatibility without sixty
    # permanent USB service connections.
    if should_continue is None:
        completed_result = await send_action_sequence_to_devices(
            ordered,
            ["wake", "home"],
        )
    else:
        completed_result = await send_action_sequence_to_devices(
            ordered,
            ["wake", "home"],
            should_continue=should_continue,
        )
    fallback = [udid for udid in ordered if not completed_result.get(udid, False)]
    legacy_result = (
        await send_legacy_action_to_devices(
            fallback,
            "wake",
            should_continue=should_continue,
        )
        if fallback
        else {}
    )
    return {
        udid: bool(completed_result.get(udid) or legacy_result.get(udid))
        for udid in ordered
    }


async def send_reliable_sleep_to_devices(
    udids: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    """Lock through acknowledged 6203, then use legacy 6000 only if needed."""
    ordered = list(dict.fromkeys(udids))
    if should_continue is None:
        verified_result = await send_action_to_devices(ordered, "sleep")
    else:
        verified_result = await send_action_sequence_to_devices(
            ordered,
            ["sleep"],
            should_continue=should_continue,
        )
    fallback = [udid for udid in ordered if not verified_result.get(udid, False)]
    legacy_result = (
        await send_legacy_action_to_devices(
            fallback,
            "sleep",
            should_continue=should_continue,
        )
        if fallback
        else {}
    )
    return {
        udid: bool(verified_result.get(udid) or legacy_result.get(udid))
        for udid in ordered
    }


class OnDemandDeviceActionHub:
    """Run all-phone power commands without keeping USB service ports open."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._state_lock = threading.Lock()
        self._desired: set[str] = set()
        self._current_operation: Future[dict[str, bool]] | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._thread_main,
            name="xinglan-action-hub",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(timeout=2.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def update_devices(self, udids: Iterable[str]) -> None:
        with self._state_lock:
            self._desired = set(dict.fromkeys(udids))

    def _submit(
        self,
        operation: Any,
        ordered: list[str],
    ) -> Future[dict[str, bool]]:
        loop = self._loop
        if self._closed or loop is None or not loop.is_running():
            if asyncio.iscoroutine(operation):
                operation.close()
            failed: Future[dict[str, bool]] = Future()
            failed.set_result({udid: False for udid in ordered})
            return failed
        with self._state_lock:
            previous = self._current_operation
            if previous is not None and not previous.done():
                previous.cancel()
            future = asyncio.run_coroutine_threadsafe(operation, loop)
            self._current_operation = future
        return future

    def broadcast(self, udids: Iterable[str], action: str) -> Future[dict[str, bool]]:
        ordered = list(dict.fromkeys(udids))
        return self._submit(send_legacy_action_to_devices(ordered, action), ordered)

    def broadcast_verified(
        self,
        udids: Iterable[str],
        action: str,
    ) -> Future[dict[str, bool]]:
        """Prefer acknowledged 6203 and use 6000 only as an on-demand fallback."""
        ordered = list(dict.fromkeys(udids))
        if action == "wake":
            operation = send_reliable_wake_to_devices(ordered)
        elif action == "sleep":
            operation = send_reliable_sleep_to_devices(ordered)
        else:
            operation = send_action_to_devices(ordered, action)
        return self._submit(operation, ordered)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        with self._state_lock:
            self._desired.clear()
            current = self._current_operation
            self._current_operation = None
        if current is not None and not current.done():
            current.cancel()
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=2.0)


async def identify_physical_device(udid: str) -> bool:
    """Turn one phone off and back on through the independent XLStream service."""
    slept = await send_device_action(udid, "sleep")
    await asyncio.sleep(0.6)
    woke = await send_device_action(udid, "wake")
    return slept and woke
