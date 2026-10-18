from datetime import date
import pytest
from warehouse_core.shifts import make_shift, shifts_between, team_colours_for_week

def test_mon_thu_shifts_and_overnight():
    d = date(2026, 10, 12)
    assert (make_shift(d,"Days")["start"].strftime("%H:%M"), make_shift(d,"Days")["end"].strftime("%H:%M")) == ("06:00","14:00")
    assert (make_shift(d,"Afters")["start"].strftime("%H:%M"), make_shift(d,"Afters")["end"].strftime("%H:%M")) == ("14:00","22:00")
    n = make_shift(d,"Nights"); assert n["start"].strftime("%H:%M") == "22:00" and n["end"].strftime("%H:%M") == "06:00" and n["end"].date() == date(2026,10,13)

def test_friday_short_shifts():
    d = date(2026,10,16)
    assert (make_shift(d,"Days")["start"].strftime("%H:%M"),make_shift(d,"Days")["end"].strftime("%H:%M")) == ("06:00","13:00")
    assert (make_shift(d,"Afters")["start"].strftime("%H:%M"),make_shift(d,"Afters")["end"].strftime("%H:%M")) == ("13:00","20:00")
    n=make_shift(d,"Nights"); assert n["start"].strftime("%H:%M")=="20:00" and n["end"].strftime("%H:%M")=="03:00" and n["end"].date()==date(2026,10,17)

def test_three_week_colour_rotation():
    assert team_colours_for_week(date(2026,10,12)) == {"Days":"Yellow","Afters":"Red","Nights":"Blue"}
    assert team_colours_for_week(date(2026,10,19)) == {"Days":"Red","Afters":"Blue","Nights":"Yellow"}
    assert team_colours_for_week(date(2026,10,26)) == {"Days":"Blue","Afters":"Yellow","Nights":"Red"}
    assert team_colours_for_week(date(2026,11,2)) == team_colours_for_week(date(2026,10,12))

def test_weekends_and_order():
    assert shifts_between(date(2026,10,17),date(2026,10,18)) == []
    assert [x["shift_name"] for x in shifts_between(date(2026,10,12),date(2026,10,12))] == ["Days","Afters","Nights"]
    with pytest.raises(ValueError): make_shift(date(2026,10,12),"Swing")
