from datetime import date, timedelta
from warehouse_core.shifts import make_shift
from warehouse_core.transport import normalize_events, events_for_shift, planned_counts, fetch_all_pages

def test_normalize_missing_end_and_invalid_intervals():
    rows=normalize_events([{"id":1,"start":"2026-10-12T07:00:00+01:00"},{"id":2,"start":None},{"id":3,"start":"2026-10-12T08:00:00+01:00","end":"2026-10-12T07:00:00+01:00"}])
    assert [r["id"] for r in rows]==[1] and rows[0]["_end"]-rows[0]["_start"]==timedelta(hours=1)

def test_strict_overlap_and_crossing_events():
    shift=make_shift(date(2026,10,12),"Days")
    events=normalize_events([{"id":"cross","start":"2026-10-12T05:30:00+01:00","end":"2026-10-12T06:30:00+01:00"},{"id":"inside","start":"2026-10-12T09:00:00+01:00","end":"2026-10-12T10:00:00+01:00"},{"id":"touch","start":"2026-10-12T14:00:00+01:00","end":"2026-10-12T15:00:00+01:00"}])
    assert [e["id"] for e in events_for_shift(events,shift)]==["cross","inside"]

def test_planned_counts_filter_shift_and_source_fields():
    shift=make_shift(date(2026,10,12),"Days")
    events=normalize_events([{"source":"Bulk","start":"2026-10-12T07:00:00+01:00","end":"2026-10-12T08:00:00+01:00"},{"title":"Campey load","start":"2026-10-12T08:00:00+01:00","end":"2026-10-12T09:00:00+01:00"},{"load_header":"DHL trunk","start":"2026-10-12T09:00:00+01:00","end":"2026-10-12T10:00:00+01:00"},{"source":"DHL","start":"2026-10-12T15:00:00+01:00","end":"2026-10-12T16:00:00+01:00"}])
    assert planned_counts(events,shift)=={"inbound_planned":1,"outbound_planned_campeys":1,"outbound_planned_dhl":1}

def test_pagination_includes_later_pages():
    rows=[{"id":i} for i in range(7)]; rows[-1]["source"]="DHL"; calls=[]
    def fetch(a,b): calls.append((a,b)); return rows[a:b+1]
    result=fetch_all_pages(fetch,3)
    assert len(result)==7 and result[-1]["source"]=="DHL" and calls==[(0,2),(3,5),(6,8)]

def test_empty_page_and_invalid_page_size():
    import pytest
    assert fetch_all_pages(lambda a,b: [],10)==[]
    with pytest.raises(ValueError): fetch_all_pages(lambda a,b: [],0)
