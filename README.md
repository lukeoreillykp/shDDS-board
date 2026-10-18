# Warehouse Shift Hub — staged refactor kit

This is a **safe starter kit**, not a replacement app and not a GitHub commit. Your working `app.py`, Supabase schemas, secrets, and deployment are intentionally untouched.

## Included
- `warehouse_core/shifts.py`: shift schedule and 3-week colour rotation.
- `warehouse_core/rules.py`: answer-colour rules including breakdown/IPS logic.
- `warehouse_core/transport.py`: timestamp normalization, shift overlap, source counts, and generic paginated fetching.
- `tests/`: schedule, colour rotation, event counts/overlap/pagination, and answer-rule tests.
- `MIGRATION_PLAN.md`: staged integration checklist.

## Run
Python 3.10+ recommended:

```bash
python -m pip install pytest
python -m pytest -q
```

The pure modules do not import Streamlit or Supabase, so tests don't require credentials or a live database.

**Do not replace the current `app.py` with these files.** They are not wired into the UI yet; migrate one concern at a time on a separate branch after reviewing tests.
