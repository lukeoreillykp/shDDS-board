import pytest
from warehouse_core.rules import answer_colour

def q(rule): return {"rules":{"colour_rule":rule}}

@pytest.mark.parametrize("value,expected",[(None,"green"),("","green"),(0,"green"),(0.0,"green")])
def test_blank_zero_breakdowns_green(value,expected): assert answer_colour(q("zero_green"),value)==expected
@pytest.mark.parametrize("value",[1,2,"1",-1])
def test_nonzero_breakdowns_red(value): assert answer_colour(q("zero_green"),value)=="red"
@pytest.mark.parametrize("breakdowns,ips,expected",[(None,None,"green"),("",0,"green"),(0,0,"green"),(0,5,"green"),(2,1,"green"),(2,"1","green"),(2,0,"red"),(2,None,"red"),(2,"","red")])
def test_ips_rule(breakdowns,ips,expected): assert answer_colour(q("ips_positive_or_no_breakdowns"),ips,answers={"breakdowns":breakdowns})==expected

def test_severity_positive_hundred_staff_rules():
    assert [answer_colour(q("severity_lmh"),v) for v in "LMH"]==["green","amber","red"]
    assert answer_colour(q("positive_green"),1)=="green" and answer_colour(q("positive_green"),0)=="red"
    assert answer_colour(q("hundred_green"),100)=="green" and answer_colour(q("hundred_green"),99)=="red"
    assert answer_colour(q("staff_under_three_red"),2)=="red" and answer_colour(q("staff_under_three_red"),3)=="green"

def test_transport_planned_match_rules():
    p={"inbound_planned":4,"outbound_planned_campeys":2,"outbound_planned_dhl":3}
    assert answer_colour(q("matches_inbound_planned"),4,p)=="green"
    assert answer_colour(q("matches_campey_planned"),1,p)=="red"
    assert answer_colour(q("matches_dhl_planned"),3,p)=="green"
    assert answer_colour(q("none"),"x") is None
