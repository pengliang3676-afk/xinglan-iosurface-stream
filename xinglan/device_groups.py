from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DeviceAssignment:
    group: int
    slot: int
    name: str = ""


class DeviceGroupStore:
    """Persistent UDID -> group/slot mapping used by the ten-device wall."""

    def __init__(self, path: Path, *, max_groups: int, group_size: int = 10) -> None:
        self.path = path
        self.max_groups = max(1, max_groups)
        self.group_size = max(1, group_size)
        self.assignments: dict[str, DeviceAssignment] = {}
        self.load()

    def load(self) -> None:
        self.assignments.clear()
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_devices, dict):
            return
        occupied: set[tuple[int, int]] = set()
        for udid, raw in raw_devices.items():
            if not isinstance(udid, str) or not isinstance(raw, dict):
                continue
            try:
                assignment = DeviceAssignment(
                    group=int(raw.get("group", 0)),
                    slot=int(raw.get("slot", 0)),
                    name=str(raw.get("name", "")).strip()[:24],
                )
            except (TypeError, ValueError):
                continue
            position = (assignment.group, assignment.slot)
            if not self._valid_position(*position) or position in occupied:
                continue
            occupied.add(position)
            self.assignments[udid] = assignment

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "devices": {
                udid: asdict(assignment)
                for udid, assignment in sorted(self.assignments.items())
            },
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def assign(self, udid: str, group: int, slot: int, name: str = "") -> None:
        if not self._valid_position(group, slot):
            raise ValueError("组号或组内编号超出范围")
        conflict = self.udid_at(group, slot)
        if conflict is not None and conflict != udid:
            raise ValueError(f"该位置已被手机 {self.label(conflict)} 占用")
        self.assignments[udid] = DeviceAssignment(group, slot, name.strip()[:24])
        self.save()

    def assignment(self, udid: str) -> DeviceAssignment | None:
        return self.assignments.get(udid)

    def udid_at(self, group: int, slot: int) -> str | None:
        for udid, assignment in self.assignments.items():
            if assignment.group == group and assignment.slot == slot:
                return udid
        return None

    def positioned_devices(
        self, udids: Iterable[str], group: int
    ) -> list[tuple[int, str]]:
        available = set(udids)
        positioned = [
            (assignment.slot, udid)
            for udid, assignment in self.assignments.items()
            if udid in available and assignment.group == group
        ]
        return sorted(positioned)

    def label(self, udid: str) -> str:
        assignment = self.assignments.get(udid)
        if assignment is not None and assignment.name:
            return assignment.name
        return udid[-8:]

    def _valid_position(self, group: int, slot: int) -> bool:
        return 1 <= group <= self.max_groups and 1 <= slot <= self.group_size
