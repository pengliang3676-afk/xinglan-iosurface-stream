from __future__ import annotations

import json
import socket
import struct
import threading
import unittest
from pathlib import Path

from xinglan.ioscpy_touch import (
    HEADER_SIZE,
    MAGIC,
    MSG_INPUT_TOUCH,
    PROTOCOL_VERSION,
    IoscpyTouchClient,
    encode_frame,
    encode_touch,
    fit_image_bounds,
    iproxy_command,
    normalize_canvas_point,
    read_frame,
)


class IoscpyTouchTests(unittest.TestCase):
    def test_frame_header_matches_ioscpy_v4(self) -> None:
        frame = encode_frame(MSG_INPUT_TOUCH, 9, b"1234567890")
        self.assertEqual(len(frame), HEADER_SIZE + 10)
        self.assertEqual(
            struct.unpack(">IHHIQQI", frame[:HEADER_SIZE]),
            (MAGIC, PROTOCOL_VERSION, MSG_INPUT_TOUCH, 0, 0, 9, 10),
        )

    def test_touch_phase_translation(self) -> None:
        for xl_kind, ioscpy_phase in ((1, 0), (2, 1), (0, 2)):
            with self.subTest(xl_kind=xl_kind):
                phase, finger, x, y = struct.unpack(
                    ">BBff", encode_touch(xl_kind, 0.25, 0.75)
                )
                self.assertEqual((phase, finger), (ioscpy_phase, 0))
                self.assertAlmostEqual(x, 0.25)
                self.assertAlmostEqual(y, 0.75)

    def test_small_and_master_share_contain_coordinate_mapping(self) -> None:
        bounds = fit_image_bounds(300, 500, 300, 600)
        self.assertEqual(bounds, (25, 0, 275, 500))
        self.assertEqual(normalize_canvas_point(150, 250, bounds), (0.5, 0.5))
        self.assertIsNone(normalize_canvas_point(10, 250, bounds))
        self.assertEqual(
            normalize_canvas_point(10, 250, bounds, clamp=True), (0.0, 0.5)
        )

    def test_iproxy_is_pinned_to_one_udid(self) -> None:
        command = iproxy_command(
            Path("C:/tools/iproxy.exe"), 49152, "00008030-000425810C43802E"
        )
        self.assertEqual(command[1:], ["49152", "27185", "-u", "00008030-000425810C43802E", "-l"])

    def test_handshake_matches_ioscpy_daemon(self) -> None:
        host, daemon = socket.socketpair()
        observed: dict[str, object] = {}

        def fake_daemon() -> None:
            try:
                msg_type, seq, payload = read_frame(daemon)
                observed["hello"] = (msg_type, seq, json.loads(payload.decode("utf-8")))
                ack = json.dumps(
                    {
                        "daemon_version": "0.1.5",
                        "protocol_version": PROTOCOL_VERSION,
                        "session_token": "formal-client-token",
                        "capabilities": {},
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                daemon.sendall(encode_frame(2, 0, ack))
                observed["auth"] = read_frame(daemon)
            finally:
                daemon.close()

        server = threading.Thread(target=fake_daemon)
        server.start()
        try:
            IoscpyTouchClient("00008030-000425810C43802E", Path("iproxy"))._handshake(host)
        finally:
            host.close()
            server.join(timeout=2.0)

        hello_type, hello_seq, hello_body = observed["hello"]
        self.assertEqual((hello_type, hello_seq), (1, 0))
        self.assertEqual(hello_body["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(observed["auth"], (5, 0, b"formal-client-token"))


if __name__ == "__main__":
    unittest.main()
