"""Build a visual 20-tile board from the engine's structured fields and board art."""
from __future__ import annotations

import re

TILE_EMOJI = {
    "start": "🏁",
    "task": "🎯",
    "truth": "💬",
    "shop": "🛒",
    "jail": "🔒",
    "chance": "🎴",
    "mystery": "❓",
}
TILE_LABEL = {
    "start": "起点",
    "task": "任务",
    "truth": "真心话",
    "shop": "商店",
    "jail": "禁闭",
    "chance": "机会",
    "mystery": "未知",
}
SPECIAL = {0: "start", 4: "truth", 5: "chance", 8: "mystery", 10: "jail",
           12: "shop", 14: "truth", 15: "chance", 17: "mystery", 19: "shop"}
SPECIAL_DENSE = {0: "start", 5: "chance", 8: "mystery", 11: "jail", 14: "truth", 17: "shop"}
EMOJI_TO_KIND = {v: k for k, v in TILE_EMOJI.items()}

# Ring layout: start at bottom-left, clockwise. 6 + 4 + 6 + 4 = 20.
RING_SLOTS = (
    [(5, c) for c in range(6)]          # bottom, left -> right: 0-5
    + [(r, 5) for r in range(4, 0, -1)]  # right, upward: 6-9
    + [(0, c) for c in range(5, -1, -1)]  # top, right -> left: 10-15
    + [(r, 0) for r in range(1, 5)]      # left, downward: 16-19
)


def _kinds_for_length(length: int | None) -> list[str]:
    special = SPECIAL_DENSE if length is not None and length <= 12 else SPECIAL
    return ["start" if i == 0 else special.get(i, "task") for i in range(20)]


def _parse_owners(status: str) -> dict[int, str]:
    owners = {}
    if not status:
        return owners
    for match in re.finditer(r"第(\d+)格\(([^)]+)\)", status):
        owners[int(match.group(1))] = match.group(2)
    return owners


def _parse_art_cells(board: str) -> list[dict]:
    if not board:
        return []
    line = board.splitlines()[0] if board else ""
    cells = []
    for match in re.finditer(r"［([^］]*)］", line):
        body = match.group(1)
        kind = "task"
        emoji = "🎯"
        for mark, name in EMOJI_TO_KIND.items():
            if mark in body:
                kind = name
                emoji = mark
                break
        cells.append({"raw": body, "kind": kind, "emoji": emoji, "tokens": re.findall(r"[🔵🔴🟢🟡🟣🟠⚫⚪]", body)})
    return cells


def _parse_hands(board: str) -> dict[str, list[str]]:
    hands = {}
    if not board:
        return hands
    for line in board.splitlines()[1:]:
        name_match = re.search(r"[🔵🔴🟢🟡🟣🟠⚫⚪]\s*([^\s@]+)@", line)
        hand_match = re.search(r"🃏［([^］]*)］", line)
        if name_match and hand_match:
            raw = hand_match.group(1).strip()
            hands[name_match.group(1)] = [] if raw in ("空", "") else [part.strip() for part in raw.split(",") if part.strip()]
    return hands


def structure(payload: dict, *, flavor: str | None = None, game_length: int | None = None) -> dict:
    positions = payload.get("positions") or payload.get("pos") or {}
    coins = payload.get("coins") or {}
    laps = payload.get("laps") or payload.get("lap") or {}
    status = payload.get("status") or ""
    board = payload.get("board") or ""
    kinds = _kinds_for_length(game_length)
    art = _parse_art_cells(board)
    if len(art) == 20:
        kinds = [cell["kind"] for cell in art]
    owners = _parse_owners(status)
    tiles = []
    for index in range(20):
        row, col = RING_SLOTS[index]
        kind = kinds[index] if index < len(kinds) else "task"
        occupants = [name for name, pos in positions.items() if pos == index]
        tiles.append({
            "index": index,
            "row": row,
            "col": col,
            "kind": kind,
            "label": TILE_LABEL.get(kind, kind),
            "emoji": TILE_EMOJI.get(kind, "•"),
            "owner": owners.get(index),
            "occupants": occupants,
        })
    # status text uses 第k格 with 1-based? "第k格(name)" - need to check. In Chinese 第0格 is odd; they said 第k格. Looking at status: `第{k}格({v})` for k, v in sorted(self.owner.items()). owner keys are tile indices 0-19. So 第0格 or 第10格. Parser is fine.
    return {
        "tiles": tiles,
        "positions": positions,
        "coins": coins,
        "laps": laps,
        "hands": _parse_hands(board),
        "turn": payload.get("turn") or payload.get("next_turn") or "",
        "flavor": flavor,
        "game_length": game_length,
        "status_text": status,
        "board_art": board,
    }
