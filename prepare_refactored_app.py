
from pathlib import Path
import re
import sys

ROOT = Path.cwd()
SOURCE = ROOT / "app.py"
OUTPUT = ROOT / "app_refactored.py"


def replace_once(source, pattern, replacement, label):
    updated, count = re.subn(
        pattern, lambda _: replacement, source, count=1
    )
    if count != 1:
        raise RuntimeError(
            f"Could not safely refactor {label}; no output written."
        )
    return updated


def main():
    if not SOURCE.is_file():
        sys.exit("ERROR: Run this script beside app.py.")

    if OUTPUT.exists():
        sys.exit(
            "ERROR: app_refactored.py already exists. "
            "Rename it before running this script again."
        )

    for path in (
        "warehouse_core/shifts.py",
        "warehouse_core/rules.py",
        "warehouse_core/transport.py",
    ):
        if not (ROOT / path).is_file():
            sys.exit(f"ERROR: Missing required module: {path}")

    source = SOURCE.read_text(encoding="utf-8")

    old_import = "from supabase import Client, create_client\n"
    if source.count(old_import) != 1:
        sys.exit("ERROR: Unexpected imports in app.py; stopped safely.")

    imports = '''from supabase import Client, create_client

from warehouse_core.shifts import (
    UK,
    COLOUR_HEX,
    SHIFT_ORDER,
    make_shift,
    shifts_between,
    team_colours_for_week,
)
from warehouse_core.rules import answer_colour
from warehouse_core.transport import (
    parse_dt,
    normalize_events,
    overlaps,
    events_for_shift,
    source_matches,
    planned_counts,
    fetch_all_pages,
)
'''
    source = source.replace(old_import, imports, 1)

    source = replace_once(
        source,
        r'UK = ZoneInfo\("Europe/London"\)\n'
        r'ROTATION_ANCHOR_MONDAY = date\(2026, 10, 12\)\n'
        r'BASE_COLOURS = \{"Days": "Yellow", "Afters": "Red", "Nights": "Blue"\}\n'
        r'COLOUR_HEX = \{\n'
        r'    "Yellow": "#E0B323",\n'
        r'    "Red": "#D64545",\n'
        r'    "Blue": "#2878C8",\n'
        r'\}\n'
        r'SHIFT_ORDER = \["Days", "Afters", "Nights"\]\n',
        "",
        "shift constants",
    )

    source = replace_once(
        source,
        r'def parse_dt\(value\) -> datetime \| None:[\s\S]*?(?=def choose_default_shift\()',
        "",
        "duplicate shift helper functions",
    )

    transport = '''def load_events(sb: Client) -> list[dict]:
    """Fetch all event pages and normalize transport events."""
    try:
        rows = fetch_all_pages(
            lambda start, end: (
                sb.table("events")
                .select("*")
                .range(start, end)
                .execute()
                .data
                or []
            ),
            page_size=1000,
        )
    except Exception as exc:
        st.warning(f"Could not load transport calendar events: {exc}")
        return []

    return normalize_events(rows)


'''
    source = replace_once(
        source,
        r'def load_events\(sb: Client\) -> list\[dict\]:[\s\S]*?(?=def cil_is_planned\()',
        transport,
        "transport helpers",
    )

    source = replace_once(
        source,
        r'def answer_colour\([\s\S]*?(?=def badge_text\()',
        "",
        "answer colour helper",
    )

    required = [
        "from warehouse_core.shifts import",
        "from warehouse_core.rules import answer_colour",
        "from warehouse_core.transport import",
        "def choose_default_shift(",
        "def adjacent_shifts(",
        "def load_events(",
        "def cil_is_planned(",
        "def main():",
    ]
    missing = [item for item in required if item not in source]
    if missing:
        sys.exit("ERROR: Validation failed: " + ", ".join(missing))

    for duplicate in (
        "def parse_dt(value)",
        "def team_colours_for_week(",
        "def make_shift(",
        "def shifts_between(",
        "def answer_colour(",
        "def planned_counts(",
        "def events_for_shift(",
        "def source_matches(",
        "def overlaps(",
    ):
        if duplicate in source:
            sys.exit(f"ERROR: Duplicate helper remains: {duplicate}")

    try:
        compile(source, str(OUTPUT), "exec")
    except SyntaxError as exc:
        sys.exit(
            f"ERROR: Generated syntax error on line "
            f"{exc.lineno}: {exc.msg}"
        )

    OUTPUT.write_text(source, encoding="utf-8")
    print(f"Created {OUTPUT.name}; original app.py is unchanged.")
    print("Run tests: python -m pytest -q")
    print("Start app: streamlit run app_refactored.py")


if __name__ == "__main__":
    main()
