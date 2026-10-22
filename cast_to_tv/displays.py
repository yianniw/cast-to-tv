"""Resolve saved monitor identities independently of capture order and geometry."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence


def list_display_ids(displays: Sequence[Mapping[str, object]]) -> list[str | None]:
    """Use MSS 10.2+'s Windows monitor interfaces; ambiguous IDs stay unset."""
    identities = []
    for display in displays:
        identity = display.get("unique_id")
        identities.append(identity.casefold() if isinstance(identity, str) and identity.strip() else None)
    counts = Counter(identity for identity in identities if identity is not None)
    return [identity if counts[identity] == 1 else None for identity in identities]


def remap_display_index(index: int, previous: Sequence[Mapping[str, object]],
                        current: Sequence[Mapping[str, object]]) -> int:
    """Follow identity across topology changes; never guess from a rectangle."""
    if not 0 <= index < len(previous):
        return -1
    identity = list_display_ids(previous)[index]
    if identity is None:
        # An unidentified selection is usable only while its snapshot is intact.
        return index if previous == current else -1
    identities = list_display_ids(current)
    return identities.index(identity) if identity in identities else -1


def same_capture_area(first: Mapping[str, object], second: Mapping[str, object]) -> bool:
    return all(first.get(key) == second.get(key) for key in ("left", "top", "width", "height"))
