# Safe migration plan

1. Preserve `main` and the currently deployed `app.py`; create `refactor/modular-structure`.
2. Add the pure modules and tests from this kit. Run `python -m pytest -q`.
3. Migrate one concern at a time by importing helpers from `warehouse_core`; compare output with the original functions.
4. Preserve `load_events` pagination at 1,000 rows, the one-hour fallback for missing event ends, and invalid interval filtering. Keep refresh behaviour that clears `selected_shift_key` before rerunning.
5. Keep the existing Supabase schema unchanged: `events` (`id,title,start,end,colour,source,status,load_header`), `shift_questions`, `navigation_links`, and `shift_submissions`. Do not apply a new setup SQL file.
6. Add mocked Supabase integration tests for >1,000 events and DHL records on later pages; verify query column names and payloads before extracting database functions.
7. Test in a separate Streamlit deployment/environment before opening a PR. Confirm management password, sidebar links, question editing, submissions, current/previous three shifts, calendar filtering, refresh, and answer colours.
8. Merge/deploy only after tests and manual checks pass.

## Acceptance checklist
- Mon–Thu: Days 06:00–14:00, Afters 14:00–22:00, Nights 22:00–06:00 next day.
- Friday: Days 06:00–13:00, Afters 13:00–20:00, Nights 20:00–03:00 Saturday.
- Week beginning 12 Oct 2026: Days Yellow, Afters Red, Nights Blue; cycle repeats every three weeks.
- Event overlap is strict: `event_start < shift_end` and `event_end > shift_start`.
- Missing event end gets a one-hour fallback; invalid intervals are ignored.
- Breakdown blank/zero green, nonzero red. IPS green if IPS > 0 or breakdowns blank/zero; otherwise red.
- Keep `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and management password in Streamlit secrets, never source control.
