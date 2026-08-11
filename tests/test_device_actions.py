from __future__ import annotations

import asyncio
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
    identify_physical_device,
    send_action_to_devices,
    send_device_action,
    send_legacy_action_to_devices,
    send_legacy_device_action,
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
