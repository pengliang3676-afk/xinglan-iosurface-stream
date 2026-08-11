from __future__ import annotations

from dataclasses import dataclass

from xinglan.control_protocol import KeyModifier


KEYBOARD_PAGE = 0x07


@dataclass(frozen=True)
class HIDKeystroke:
    page: int
    usage: int
    modifiers: int = 0


_UNSHIFTED = {
    "1": 0x1E,
    "2": 0x1F,
    "3": 0x20,
    "4": 0x21,
    "5": 0x22,
    "6": 0x23,
    "7": 0x24,
    "8": 0x25,
    "9": 0x26,
    "0": 0x27,
    " ": 0x2C,
    "-": 0x2D,
    "=": 0x2E,
    "[": 0x2F,
    "]": 0x30,
    "\\": 0x31,
    ";": 0x33,
    "'": 0x34,
    "`": 0x35,
    ",": 0x36,
    ".": 0x37,
    "/": 0x38,
}

_SHIFTED = {
    "!": 0x1E,
    "@": 0x1F,
    "#": 0x20,
    "$": 0x21,
    "%": 0x22,
    "^": 0x23,
    "&": 0x24,
    "*": 0x25,
    "(": 0x26,
    ")": 0x27,
    "_": 0x2D,
    "+": 0x2E,
    "{": 0x2F,
    "}": 0x30,
    "|": 0x31,
    ":": 0x33,
    '"': 0x34,
    "~": 0x35,
    "<": 0x36,
    ">": 0x37,
    "?": 0x38,
}

_SPECIAL = {
    "Return": 0x28,
    "KP_Enter": 0x28,
    "Escape": 0x29,
    "BackSpace": 0x2A,
    "Tab": 0x2B,
    "Insert": 0x49,
    "Home": 0x4A,
    "Prior": 0x4B,
    "Delete": 0x4C,
    "End": 0x4D,
    "Next": 0x4E,
    "Right": 0x4F,
    "Left": 0x50,
    "Down": 0x51,
    "Up": 0x52,
}
_SPECIAL.update({f"F{index}": 0x39 + index for index in range(1, 13)})


def map_keypress(keysym: str, char: str) -> HIDKeystroke | None:
    """Map one Tk key press to a standard USB HID keyboard stroke.

    Chinese composition intentionally stays on the phone: the PC sends raw
    Latin keys and iOS' selected hardware-keyboard input method performs the
    Pinyin composition. Non-ASCII committed PC IME text is therefore ignored.
    """

    if len(char) == 1:
        if "a" <= char <= "z":
            return HIDKeystroke(KEYBOARD_PAGE, 0x04 + ord(char) - ord("a"))
        if "A" <= char <= "Z":
            return HIDKeystroke(
                KEYBOARD_PAGE,
                0x04 + ord(char) - ord("A"),
                int(KeyModifier.SHIFT),
            )
        usage = _UNSHIFTED.get(char)
        if usage is not None:
            return HIDKeystroke(KEYBOARD_PAGE, usage)
        usage = _SHIFTED.get(char)
        if usage is not None:
            return HIDKeystroke(KEYBOARD_PAGE, usage, int(KeyModifier.SHIFT))

    usage = _SPECIAL.get(keysym)
    if usage is not None:
        return HIDKeystroke(KEYBOARD_PAGE, usage)
    return None
