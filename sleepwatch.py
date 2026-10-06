#!/usr/bin/env python3
"""pmset の電源ログから、画面OFFとスリープを日ごとの文字帯で表示する。"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) [+-]\d{4}\s+(?P<rest>.+)$"
)
KIND_RE = re.compile(r"^(?P<kind>.+?)\s{2,}(?P<msg>.+)$")
WEEKDAYS = "月火水木金土日"
LABEL_COLUMNS = 6
DAY_SECONDS = 24 * 60 * 60
RESET = "\033[0m"
DIM = "\033[2m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

GLYPH = {
    "off": "░",
    "on": "█",
    "asleep": "░",
    "awake": "█",
    "unknown": "·",
    "future": " ",
}
DISPLAY_COLOR = {"on": YELLOW, "off": DIM, "unknown": DIM}
POWER_COLOR = {"awake": CYAN, "asleep": DIM, "unknown": DIM}


@dataclass(frozen=True)
class Event:
    ts: datetime
    kind: str


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def split_kind_message(rest: str) -> tuple[str, str] | None:
    """区分はタブの前まで。Wake Requests を Wake と分けて読む。"""
    if "\t" in rest:
        kind, message = rest.split("\t", 1)
        kind = kind.strip()
        message = message.strip()
        if kind and message:
            return kind, message
        return None
    match = KIND_RE.match(rest.strip())
    if match is None:
        return None
    return match.group("kind").strip(), match.group("msg").strip()


def classify(kind: str, message: str) -> str | None:
    if "Display is turned off" in message:
        return "display_off"
    if "Display is turned on" in message:
        return "display_on"
    if "Entering Sleep" in message:
        return "sleep"
    # DarkWake や Wake Requests は区分が Wake ではない。
    if kind == "Wake":
        return "wake"
    return None


def parse_events(text: str) -> list[Event]:
    events: list[Event] = []
    for raw in text.splitlines():
        line = strip_ansi(raw).strip()
        match = LINE_RE.match(line)
        if match is None:
            continue
        parts = split_kind_message(match.group("rest"))
        if parts is None:
            continue
        kind = classify(*parts)
        if kind is None:
            continue
        events.append(Event(datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S"), kind))
    events.sort(key=lambda event: event.ts)
    return events


def state_changes(events: list[Event], start_kind: str, end_kind: str, start_state: str, end_state: str) -> list[tuple[datetime, str]]:
    """start で start_state、end で end_state になる変化点。同じ状態の繰り返しは捨てる。"""
    changes: list[tuple[datetime, str]] = []
    state = "unknown"
    for event in events:
        if event.kind == start_kind and state != start_state:
            state = start_state
            changes.append((event.ts, state))
        elif event.kind == end_kind and state != end_state:
            state = end_state
            changes.append((event.ts, state))
    return changes


def display_changes(events: list[Event]) -> list[tuple[datetime, str]]:
    return state_changes(events, "display_off", "display_on", "off", "on")


def power_changes(events: list[Event]) -> list[tuple[datetime, str]]:
    """Entering Sleep から、区分 Wake の Full Wake までをスリープにする。"""
    return state_changes(events, "sleep", "wake", "asleep", "awake")


def _state_at(changes: list[tuple[datetime, str]], moment: datetime) -> tuple[str, int]:
    state = "unknown"
    index = 0
    while index < len(changes) and changes[index][0] <= moment:
        state = changes[index][1]
        index += 1
    return state, index


def dominant(changes: list[tuple[datetime, str]], start: datetime, end: datetime, prefer: str, other: str) -> str:
    """区間に prefer が少しでもあれば prefer。なければ other、どちらもなければ unknown。"""
    state, index = _state_at(changes, start)
    seen = {state}
    while index < len(changes) and changes[index][0] < end:
        seen.add(changes[index][1])
        index += 1
    if prefer in seen:
        return prefer
    if other in seen:
        return other
    return "unknown"


def covered_seconds(changes: list[tuple[datetime, str]], start: datetime, end: datetime, target: str) -> float:
    if end <= start:
        return 0.0
    state, index = _state_at(changes, start)
    cursor = start
    total = 0.0
    while index < len(changes) and changes[index][0] < end:
        moment, new_state = changes[index]
        if state == target:
            total += (moment - cursor).total_seconds()
        cursor = moment
        state = new_state
        index += 1
    if state == target:
        total += (end - cursor).total_seconds()
    return total


def cell_bounds(day: date, index: int, count: int) -> tuple[datetime, datetime]:
    origin = datetime.combine(day, time.min)
    start = origin + timedelta(seconds=(DAY_SECONDS * index) // count)
    end = origin + timedelta(seconds=(DAY_SECONDS * (index + 1)) // count)
    return start, end


def day_window(day: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    if day > now.date():
        return start, start
    if day == now.date():
        return start, min(end, now)
    return start, end


def bar_states(
    changes: list[tuple[datetime, str]],
    day: date,
    count: int,
    prefer: str,
    other: str,
    now: datetime,
) -> list[str]:
    states: list[str] = []
    for index in range(count):
        start, end = cell_bounds(day, index, count)
        if day > now.date() or (day == now.date() and start >= now):
            states.append("future")
            continue
        if day == now.date() and end > now:
            end = now
        states.append(dominant(changes, start, end, prefer, other))
    return states


def render_axis(count: int) -> str:
    if count < 2:
        return " " * max(count, 0)
    if count >= 26:
        hours = list(range(0, 25, 3))
    elif count >= 14:
        hours = [0, 6, 12, 18, 24]
    else:
        hours = [0, 12, 24]
    line = [" "] * count
    occupied = [False] * count
    specs: list[tuple[int, int, str]] = []
    for hour in hours:
        label = f"{hour:02d}"
        if hour == 0:
            pos = 0
        elif hour == 24:
            pos = count - 2
        else:
            pos = int(round(hour / 24 * count - 1))
        pos = max(0, min(pos, count - 2))
        specs.append((hour, pos, label))
    specs.sort(key=lambda item: (item[0] not in (0, 24), item[0]))
    for _, pos, label in specs:
        if occupied[pos] or occupied[pos + 1]:
            continue
        line[pos] = label[0]
        line[pos + 1] = label[1]
        occupied[pos] = occupied[pos + 1] = True
    return "".join(line)


def fit_label(text: str) -> str:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    if width >= LABEL_COLUMNS:
        return text
    return text + " " * (LABEL_COLUMNS - width)


def paint(states: list[str], color: bool, palette: dict[str, str]) -> str:
    if not color:
        return "".join(GLYPH[state] for state in states)
    parts: list[str] = []
    for state in states:
        glyph = GLYPH[state]
        code = palette.get(state)
        if code is None:
            parts.append(glyph)
        else:
            parts.append(f"{code}{glyph}{RESET}")
    return "".join(parts)


def format_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}h"


def iter_days(today: date, days: int, only: date | None) -> list[date]:
    if only is not None:
        return [only]
    if days < 1:
        raise ValueError("日数は1以上にしてください。")
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def bar_columns(width: int) -> int:
    return max(8, width - LABEL_COLUMNS)


def render(
    events: list[Event],
    days: list[date],
    width: int,
    color: bool,
    now: datetime,
    legend: bool = True,
) -> str:
    columns = bar_columns(width)
    display = display_changes(events)
    power = power_changes(events)
    blocks: list[str] = []
    for day in days:
        start, end = day_window(day, now)
        on_hours = format_hours(covered_seconds(display, start, end, "on"))
        sleep_hours = format_hours(covered_seconds(power, start, end, "asleep"))
        header = f"{day.isoformat()}（{WEEKDAYS[day.weekday()]}）  画面ON {on_hours}  スリープ {sleep_hours}"
        display_bar = paint(
            bar_states(display, day, columns, "off", "on", now),
            color,
            DISPLAY_COLOR,
        )
        power_bar = paint(
            bar_states(power, day, columns, "asleep", "awake", now),
            color,
            POWER_COLOR,
        )
        blocks.append(
            "\n".join(
                [
                    header,
                    fit_label("") + render_axis(columns),
                    fit_label("画面") + display_bar,
                    fit_label("電源") + power_bar,
                ]
            )
        )
    text = "\n\n".join(blocks)
    if legend:
        legend_line = "█ 画面ON/稼働   ░ 画面OFF/スリープ   · ログ開始前"
        text = legend_line if not text else text + "\n\n" + legend_line
    if not text:
        return ""
    return text + "\n"


def read_pmset_log() -> str:
    completed = subprocess.run(
        ["pmset", "-g", "log"],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return completed.stdout


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="画面OFFとスリープを日ごとの帯で表示する。")
    parser.add_argument("-d", "--days", type=int, default=1, help="今日を含む直近の日数（既定: 1）")
    parser.add_argument("--date", type=date.fromisoformat, help="この日だけ表示する（YYYY-MM-DD）。指定時は -d を使わない")
    parser.add_argument("--width", type=int, help="表示幅。省略時はターミナル幅")
    parser.add_argument("--no-color", action="store_true", help="色を付けない")
    parser.add_argument("--no-legend", action="store_true", help="末尾の凡例を出さない")
    args = parser.parse_args(argv)
    if args.date is None and args.days < 1:
        parser.error("日数は1以上にしてください。")
    if args.width is not None and args.width < LABEL_COLUMNS + 8:
        parser.error(f"表示幅は{LABEL_COLUMNS + 8}以上にしてください。")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        log = read_pmset_log()
    except FileNotFoundError:
        print("pmset が見つかりません。", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print("pmset -g log が失敗しました。", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1
    now = datetime.now().replace(microsecond=0)
    days = iter_days(now.date(), args.days, args.date)
    width = args.width or shutil.get_terminal_size(fallback=(80, 24)).columns
    color = sys.stdout.isatty() and not args.no_color
    sys.stdout.write(render(parse_events(log), days, width, color, now, legend=not args.no_legend))
    return 0


if __name__ == "__main__":
    sys.exit(main())
