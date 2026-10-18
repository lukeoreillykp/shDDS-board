"""Shift schedule and rotating team colours, independent of Streamlit."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
ROTATION_ANCHOR_MONDAY = date(2026, 10, 12)
BASE_COLOURS = {"Days": "Yellow", "Afters": "Red", "Nights": "Blue"}
COLOUR_HEX = {"Yellow": "#E0B323", "Red": "#D64545", "Blue": "#2878C8"}
SHIFT_ORDER = ["Days", "Afters", "Nights"]

def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())

def team_colours_for_week(day: date) -> dict[str, str]:
    weeks = (week_monday(day) - ROTATION_ANCHOR_MONDAY).days // 7
    colours = dict(BASE_COLOURS)
    for _ in range(weeks % 3):
        colours = {"Days": colours["Afters"], "Afters": colours["Nights"], "Nights": colours["Days"]}
    return colours

def make_shift(start_day: date, shift_name: str) -> dict:
    if shift_name not in SHIFT_ORDER:
        raise ValueError(f"Unknown shift name: {shift_name}")
    if shift_name == "Days":
        start_t, end_t = time(6), time(13) if start_day.weekday() == 4 else time(14)
        end_day = start_day
    elif shift_name == "Afters":
        start_t, end_t = (time(13), time(20)) if start_day.weekday() == 4 else (time(14), time(22))
        end_day = start_day
    else:
        start_t, end_t = (time(20), time(3)) if start_day.weekday() == 4 else (time(22), time(6))
        end_day = start_day + timedelta(days=1)
    start_dt = datetime.combine(start_day, start_t, tzinfo=UK)
    end_dt = datetime.combine(end_day, end_t, tzinfo=UK)
    colour = team_colours_for_week(start_day)[shift_name]
    return {"shift_date": start_day.isoformat(), "shift_name": shift_name, "start": start_dt, "end": end_dt,
            "team_colour": colour, "key": start_dt.isoformat(),
            "label": f"{start_day.strftime('%a %d %b')} · {shift_name} · {colour} team"}

def shifts_between(start_day: date, end_day: date) -> list[dict]:
    result, day = [], start_day
    while day <= end_day:
        if day.weekday() < 5:
            result.extend(make_shift(day, name) for name in SHIFT_ORDER)
        day += timedelta(days=1)
    return sorted(result, key=lambda shift: shift["start"])
