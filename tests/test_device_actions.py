from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from xinglan.control_protocol import (
    ACK,
    CONTROL_MAGIC,
    HEADER,
    HELLO,
    PROTOCOL_VERSION,
    MessageType,
    SystemAction,
    pack_message,
    unpack_header,
)
from xinglan.device_actions import (
    ACTION_MAP,
    LEGACY_COMMAND_BYTES,
    LEGACY_CONTROL_PORT,
    ON_DEMAND_MAX_CONNECTIONS,
    OnDemandDeviceActionHub,
    identify_physical_device,
    send_action_to_devices,
    send_action_sequence_to_devices,
    send_device_action,
    send_device_action_sequence,
    send_legacy_action_to_devices,
    send_legacy_device_action,
    send_reliable_sleep_to_devices,
    send_reliable_wake_to_devices,
)


def successful_response() -> bytes:
    hello = HELLO.pack(0x3F, 360, 640, PROTOCOL_VERSION, 0)
    return b"".join(
        [
            pack_message(CONTROL_MAGIC, MessageType.HELLO_ACK, 1, hello),
            pack_message(CONTROL_MAGIC, MessageType.ACK, 2, ACK.pack(2, 0)),
        ]
    )


class FakeConnection:
    def __init__(self, response: bytes | None = None) -> None:
        self.payloads: list[bytes] = []
        self.response = response if response is not None else successful_response()
        self.closed = False

    async def sendall(self, payload: bytes) -> None:
        self.payloads.append(payload)

    async def recv_any(self, length: int) -> bytes:
        value, self.response = self.response[:length], self.response[length:]
        return value

    async def close(self) -> None:
        self.closed = True


class DeviceActionTests(unittest.TestCase):
    def test_actions_use_new_binary_system_action_protocol(self) -> None:
        self.assertEqual(SystemAction.HOME, ACTION_MAP["home"])
        self.assertEqual(SystemAction.WAKE, ACTION_MAP["wake"])
        self.assertEqual(SystemAction.LOCK, ACTION_MAP["sleep"])
        self.assertEqual(SystemAction.APP_SWITCHER, ACTION_MAP["switch"])

    def test_send_device_action_uses_independent_control_port(self) -> None:
        connection = FakeConnection()
        create = AsyncMock(return_value=connection)
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=create,
        ):
            result = asyncio.run(send_device_action("device-01", "wake"))

        self.assertTrue(result)
        create.assert_awaited_once_with(
            "device-01", 6203, connection_type="USB"
        )
        self.assertEqual(2, len(connection.payloads))
        hello_header = unpack_header(connection.payloads[0][: HEADER.size], CONTROL_MAGIC)
        action_header = unpack_header(connection.payloads[1][: HEADER.size], CONTROL_MAGIC)
        self.assertEqual(MessageType.HELLO, hello_header.message_type)
        self.assertEqual(MessageType.SYSTEM_ACTION, action_header.message_type)
        self.assertTrue(connection.closed)

    def test_action_sequence_reuses_one_connection_for_wake_and_home(self) -> None:
        hello = HELLO.pack(0x3F, 360, 640, PROTOCOL_VERSION, 0)
        response = b"".join(
            [
                pack_message(CONTROL_MAGIC, MessageType.HELLO_ACK, 1, hello),
                pack_message(CONTROL_MAGIC, MessageType.ACK, 2, ACK.pack(2, 0)),
                pack_message(CONTROL_MAGIC, MessageType.ACK, 3, ACK.pack(3, 0)),
            ]
        )
        connection = FakeConnection(response)
        create = AsyncMock(return_value=connection)
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=create,
        ):
            result = asyncio.run(
                send_device_action_sequence("device-01", ["wake", "home"])
            )

        self.assertTrue(result)
        create.assert_awaited_once()
        self.assertEqual(3, len(connection.payloads))
        self.assertEqual(
            [
                MessageType.HELLO,
                MessageType.SYSTEM_ACTION,
                MessageType.SYSTEM_ACTION,
            ],
            [
                unpack_header(payload[: HEADER.size], CONTROL_MAGIC).message_type
                for payload in connection.payloads
            ],
        )
        self.assertTrue(connection.closed)

    def test_cancelled_wake_sequence_never_sends_late_home(self) -> None:
        hello = HELLO.pack(0x3F, 360, 640, PROTOCOL_VERSION, 0)
        response = b"".join(
            [
                pack_message(CONTROL_MAGIC, MessageType.HELLO_ACK, 1, hello),
                pack_message(CONTROL_MAGIC, MessageType.ACK, 2, ACK.pack(2, 0)),
            ]
        )
        connection = FakeConnection(response)
        create = AsyncMock(return_value=connection)
        checks = iter([True, True, False])
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=create,
        ):
            result = asyncio.run(
                send_device_action_sequence(
                    "device-01",
                    ["wake", "home"],
                    should_continue=lambda: next(checks),
                )
            )

        self.assertFalse(result)
        self.assertEqual(2, len(connection.payloads))
        self.assertEqual(
            [MessageType.HELLO, MessageType.SYSTEM_ACTION],
            [
                unpack_header(payload[: HEADER.size], CONTROL_MAGIC).message_type
                for payload in connection.payloads
            ],
        )
        self.assertTrue(connection.closed)

    def test_rejected_action_returns_false(self) -> None:
        hello = HELLO.pack(0x3F, 360, 640, PROTOCOL_VERSION, 0)
        response = b"".join(
            [
                pack_message(CONTROL_MAGIC, MessageType.HELLO_ACK, 1, hello),
                pack_message(CONTROL_MAGIC, MessageType.ACK, 2, ACK.pack(2, 4)),
            ]
        )
        connection = FakeConnection(response)
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=AsyncMock(return_value=connection),
        ):
            result = asyncio.run(send_device_action("device-01", "sleep"))
        self.assertFalse(result)

    def test_send_action_to_devices_deduplicates_udids(self) -> None:
        sender = AsyncMock(return_value=True)
        with patch("xinglan.device_actions.send_device_action", new=sender):
            result = asyncio.run(
                send_action_to_devices(["a", "b", "a"], "sleep")
            )

        self.assertEqual(result, {"a": True, "b": True})
        self.assertEqual(sender.await_count, 2)

    def test_legacy_top_buttons_use_exact_old_port_and_payloads(self) -> None:
        for action, payload in (("wake", b"14\r\n"), ("sleep", b"15\r\n")):
            with self.subTest(action=action):
                connection = FakeConnection()
                create = AsyncMock(return_value=connection)
                with patch(
                    "xinglan.device_actions.ServiceConnection.create_using_usbmux",
                    new=create,
                ):
                    result = asyncio.run(send_legacy_device_action("device-01", action))

                self.assertTrue(result)
                self.assertEqual(6000, LEGACY_CONTROL_PORT)
                self.assertEqual(payload, LEGACY_COMMAND_BYTES[action])
                create.assert_awaited_once_with(
                    "device-01", 6000, connection_type="USB"
                )
                self.assertEqual([payload], connection.payloads)
                self.assertTrue(connection.closed)

    def test_legacy_top_buttons_broadcast_to_all_phones_together(self) -> None:
        sender = AsyncMock(return_value=True)
        with patch("xinglan.device_actions.send_legacy_device_action", new=sender):
            result = asyncio.run(
                send_legacy_action_to_devices(["a", "b", "a", "c"], "wake")
            )

        self.assertEqual(result, {"a": True, "b": True, "c": True})
        self.assertEqual(sender.await_count, 3)

    def test_device_inventory_does_not_open_any_usb_service(self) -> None:
        create = AsyncMock()
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=create,
        ):
            hub = OnDemandDeviceActionHub()
            try:
                hub.update_devices(["device-01"])
                time.sleep(0.1)
            finally:
                hub.close()

        create.assert_not_awaited()

    def test_on_demand_legacy_broadcast_closes_connection(self) -> None:
        connection = FakeConnection()
        create = AsyncMock(return_value=connection)
        with patch(
            "xinglan.device_actions.ServiceConnection.create_using_usbmux",
            new=create,
        ):
            hub = OnDemandDeviceActionHub()
            try:
                result = hub.broadcast(["device-01"], "sleep").result(timeout=3.0)
            finally:
                hub.close()

        self.assertEqual({"device-01": True}, result)
        create.assert_awaited_once_with("device-01", 6000, connection_type="USB")
        self.assertEqual([b"15\r\n"], connection.payloads)
        self.assertTrue(connection.closed)

    def test_reliable_wake_verifies_then_returns_every_phone_home(self) -> None:
        completed = AsyncMock(return_value={"a": True, "b": True, "c": True})
        legacy = AsyncMock()
        with (
            patch("xinglan.device_actions.send_legacy_action_to_devices", new=legacy),
            patch("xinglan.device_actions.send_action_sequence_to_devices", new=completed),
        ):
            result = asyncio.run(
                send_reliable_wake_to_devices(["a", "b", "a", "c"])
            )

        self.assertEqual(result, {"a": True, "b": True, "c": True})
        legacy.assert_not_awaited()
        completed.assert_awaited_once_with(
            ["a", "b", "c"],
            ["wake", "home"],
        )

    def test_reliable_wake_keeps_legacy_compatibility(self) -> None:
        legacy = AsyncMock(return_value={"old": True})
        completed = AsyncMock(return_value={"old": False, "new": True})
        with (
            patch("xinglan.device_actions.send_legacy_action_to_devices", new=legacy),
            patch("xinglan.device_actions.send_action_sequence_to_devices", new=completed),
        ):
            result = asyncio.run(send_reliable_wake_to_devices(["old", "new"]))

        self.assertEqual(result, {"old": True, "new": True})
        legacy.assert_awaited_once_with(
            ["old"],
            "wake",
            should_continue=None,
        )

    def test_reliable_sleep_uses_6000_only_for_6203_failures(self) -> None:
        legacy = AsyncMock(return_value={"c": True})
        verified = AsyncMock(return_value={"a": True, "b": True, "c": False})
        with (
            patch("xinglan.device_actions.send_legacy_action_to_devices", new=legacy),
            patch("xinglan.device_actions.send_action_to_devices", new=verified),
        ):
            result = asyncio.run(
                send_reliable_sleep_to_devices(["a", "b", "a", "c"])
            )

        self.assertEqual(result, {"a": True, "b": True, "c": True})
        legacy.assert_awaited_once_with(
            ["c"],
            "sleep",
            should_continue=None,
        )
        verified.assert_awaited_once_with(["a", "b", "c"], "sleep")

    def test_on_demand_hub_routes_verified_wake_without_persistent_channels(self) -> None:
        reliable = AsyncMock(return_value={"a": True, "b": True})
        with patch(
            "xinglan.device_actions.send_reliable_wake_to_devices",
            new=reliable,
        ):
            hub = OnDemandDeviceActionHub()
            try:
                hub.update_devices(["a", "b"])
                result = hub.broadcast_verified(["a", "b", "a"], "wake").result(
                    timeout=3.0
                )
            finally:
                hub.close()

        self.assertEqual({"a": True, "b": True}, result)
        reliable.assert_awaited_once_with(["a", "b"])

    def test_on_demand_connections_are_bounded(self) -> None:
        active = 0
        peak = 0

        async def sender(udid: str, action: str) -> bool:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return True

        with patch("xinglan.device_actions.send_device_action", new=sender):
            result = asyncio.run(
                send_action_to_devices(
                    [f"device-{index:02d}" for index in range(25)],
                    "sleep",
                )
            )

        self.assertTrue(all(result.values()))
        self.assertEqual(ON_DEMAND_MAX_CONNECTIONS, peak)

    def test_identify_sleeps_then_wakes_phone_once(self) -> None:
        sender = AsyncMock(return_value=True)
        sleeper = AsyncMock()
        with (
            patch("xinglan.device_actions.send_device_action", new=sender),
            patch("xinglan.device_actions.asyncio.sleep", new=sleeper),
        ):
            result = asyncio.run(identify_physical_device("device-01"))

        self.assertTrue(result)
        self.assertEqual(
            [call.args[1] for call in sender.await_args_list],
            ["sleep", "wake"],
        )


if __name__ == "__main__":
    unittest.main()
