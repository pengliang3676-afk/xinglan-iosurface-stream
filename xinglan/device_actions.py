from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
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

from pymobiledevice3.service_connection import ServiceConnection  # noqa: E402


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


@dataclass
class _QueuedAction:
    action: str
    result: asyncio.Future[bool]


@dataclass
class _PersistentChannel:
    queue: asyncio.Queue[_QueuedAction]
    ready: asyncio.Event


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


async def send_action_sequence_to_devices(
    udids: Iterable[str],
    actions: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    ordered = list(dict.fromkeys(udids))
    sequence = list(actions)
    semaphore = asyncio.Semaphore(64)

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
    """Mirror Promise.all from the legacy browser backend for all USB phones."""
    ordered = list(dict.fromkeys(udids))
    if should_continue is None:
        operations = (
            send_legacy_device_action(udid, action) for udid in ordered
        )
    else:
        operations = (
            send_legacy_device_action(
                udid,
                action,
                should_continue=should_continue,
            )
            for udid in ordered
        )
    results = await asyncio.gather(*operations, return_exceptions=True)
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
    # Start both wake paths in the same event-loop turn.  Do not wait before
    # the verified path: that delay was visible on phones missed by port 6000.
    if should_continue is None:
        legacy_operation = send_legacy_action_to_devices(ordered, "wake")
        completed_operation = send_action_sequence_to_devices(
            ordered,
            ["wake", "home"],
        )
    else:
        legacy_operation = send_legacy_action_to_devices(
            ordered,
            "wake",
            should_continue=should_continue,
        )
        completed_operation = send_action_sequence_to_devices(
            ordered,
            ["wake", "home"],
            should_continue=should_continue,
        )
    legacy_task = asyncio.create_task(legacy_operation)
    completed_result = await completed_operation
    legacy_result = await legacy_task
    return {
        udid: bool(completed_result.get(udid) or legacy_result.get(udid))
        for udid in ordered
    }


async def send_reliable_sleep_to_devices(
    udids: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, bool]:
    """Lock every phone quickly and verify missed devices on the new channel.

    Port 6000 preserves the legacy all-at-once behaviour.  The acknowledged
    XLStream action runs in the same event-loop turn and repairs phones whose
    legacy write was accepted by USB but not handled by the phone.
    """
    ordered = list(dict.fromkeys(udids))
    if should_continue is None:
        legacy_operation = send_legacy_action_to_devices(ordered, "sleep")
        verified_operation = send_action_to_devices(ordered, "sleep")
    else:
        legacy_operation = send_legacy_action_to_devices(
            ordered,
            "sleep",
            should_continue=should_continue,
        )
        verified_operation = send_action_sequence_to_devices(
            ordered,
            ["sleep"],
            should_continue=should_continue,
        )
    legacy_task = asyncio.create_task(legacy_operation)
    verified_result = await verified_operation
    legacy_result = await legacy_task
    return {
        udid: bool(verified_result.get(udid) or legacy_result.get(udid))
        for udid in ordered
    }


class PersistentDeviceActionHub:
    """Keep one legacy port-6000 USB channel ready for every phone.

    The top power buttons must not create sixty usbmux connections at click
    time.  Connections are established in the background after discovery and
    the exact legacy ``14``/``15`` packet is broadcast over the ready channels
    in one event-loop turn.  There is deliberately no second command and no
    post-action verification.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._desired: set[str] = set()
        self._channels: dict[str, _PersistentChannel] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
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
        loop = self._loop
        if self._closed or loop is None or not loop.is_running():
            return
        desired = set(dict.fromkeys(udids))
        asyncio.run_coroutine_threadsafe(self._update_devices(desired), loop)

    async def _update_devices(self, desired: set[str]) -> None:
        self._desired = desired
        removed = set(self._tasks) - desired
        for udid in removed:
            task = self._tasks.pop(udid)
            task.cancel()
            self._channels.pop(udid, None)
        for position, udid in enumerate(sorted(desired - set(self._tasks))):
            channel = _PersistentChannel(asyncio.Queue(maxsize=8), asyncio.Event())
            self._channels[udid] = channel
            self._tasks[udid] = asyncio.create_task(
                self._supervise_channel(udid, channel, position * 0.025)
            )

    async def _supervise_channel(
        self,
        udid: str,
        channel: _PersistentChannel,
        initial_delay: float,
    ) -> None:
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        delay = 0.15
        pending: _QueuedAction | None = None
        while udid in self._desired:
            connection: Any | None = None
            disconnected: asyncio.Task[bytes] | None = None
            try:
                connection = await asyncio.wait_for(
                    ServiceConnection.create_using_usbmux(
                        udid,
                        LEGACY_CONTROL_PORT,
                        connection_type="USB",
                    ),
                    timeout=3.0,
                )
                channel.ready.set()
                delay = 0.15
                disconnected = asyncio.create_task(connection.recv_any(1))
                while udid in self._desired:
                    queued = asyncio.create_task(channel.queue.get())
                    done, _ = await asyncio.wait(
                        (queued, disconnected),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if disconnected in done:
                        if queued in done:
                            pending = queued.result()
                        else:
                            queued.cancel()
                            await asyncio.gather(queued, return_exceptions=True)
                        raise ConnectionError("legacy control channel closed")

                    pending = queued.result()
                    payload = LEGACY_COMMAND_BYTES.get(pending.action)
                    if payload is None:
                        if not pending.result.done():
                            pending.result.set_result(False)
                        pending = None
                        continue
                    await connection.sendall(payload)
                    if not pending.result.done():
                        pending.result.set_result(True)
                    pending = None
            except asyncio.CancelledError:
                raise
            except Exception:
                channel.ready.clear()
                if pending is not None and not pending.result.done():
                    pending.result.set_result(False)
                pending = None
                await asyncio.sleep(delay)
                delay = min(2.0, delay * 1.6)
            finally:
                channel.ready.clear()
                if disconnected is not None and not disconnected.done():
                    disconnected.cancel()
                    await asyncio.gather(disconnected, return_exceptions=True)
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass

    def broadcast(self, udids: Iterable[str], action: str) -> Future[dict[str, bool]]:
        loop = self._loop
        ordered = list(dict.fromkeys(udids))
        if self._closed or loop is None or not loop.is_running():
            failed: Future[dict[str, bool]] = Future()
            failed.set_result({udid: False for udid in ordered})
            return failed
        return asyncio.run_coroutine_threadsafe(
            self._broadcast(ordered, action),
            loop,
        )

    async def _broadcast(self, udids: list[str], action: str) -> dict[str, bool]:
        async def send_one(udid: str) -> bool:
            channel = self._channels.get(udid)
            if channel is None:
                return False
            try:
                await asyncio.wait_for(channel.ready.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                return False
            result = asyncio.get_running_loop().create_future()
            try:
                channel.queue.put_nowait(_QueuedAction(action, result))
            except asyncio.QueueFull:
                return False
            try:
                return await asyncio.wait_for(result, timeout=3.0)
            except asyncio.TimeoutError:
                return False

        results = await asyncio.gather(
            *(send_one(udid) for udid in udids),
            return_exceptions=True,
        )
        return {
            udid: bool(result) if not isinstance(result, BaseException) else False
            for udid, result in zip(udids, results)
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def shutdown() -> None:
            self._desired.clear()
            tasks = list(self._tasks.values())
            self._tasks.clear()
            self._channels.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
        try:
            future.result(timeout=2.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=2.0)


async def identify_physical_device(udid: str) -> bool:
    """Turn one phone off and back on through the independent XLStream service."""
    slept = await send_device_action(udid, "sleep")
    await asyncio.sleep(0.6)
    woke = await send_device_action(udid, "wake")
    return slept and woke
