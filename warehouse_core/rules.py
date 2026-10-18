"""Answer-colour rules used by shift reports."""
def answer_colour(question: dict, value, planned: dict | None = None, answers: dict | None = None) -> str | None:
    planned, answers = planned or {}, answers or {}
    rule = (question.get("rules") or {}).get("colour_rule", "none")
    if rule == "ips_positive_or_no_breakdowns":
        try: breakdowns = float(answers.get("breakdowns")) if answers.get("breakdowns") not in (None, "") else 0
        except (TypeError, ValueError): breakdowns = 0
        if breakdowns <= 0: return "green"
        try: ips = float(value) if value not in (None, "") else 0
        except (TypeError, ValueError): ips = 0
        return "green" if ips > 0 else "red"
    if value is None or value == "":
        if rule == "zero_green": return "green"
        if rule in ("positive_green", "staff_under_three_red"): return "red"
        return None
    try: number = float(value)
    except (TypeError, ValueError): number = None
    if rule == "severity_lmh": return {"L": "green", "M": "amber", "H": "red"}.get(str(value).upper())
    if rule == "zero_green" and number is not None: return "green" if number == 0 else "red"
    if rule == "positive_green" and number is not None: return "green" if number >= 1 else "red"
    if rule == "hundred_green" and number is not None: return "green" if number >= 100 else "red"
    if rule == "staff_under_three_red" and number is not None: return "red" if number < 3 else "green"
    match_map = {"matches_inbound_planned": "inbound_planned", "matches_campey_planned": "outbound_planned_campeys", "matches_dhl_planned": "outbound_planned_dhl"}
    if rule in match_map and number is not None: return "green" if number == planned.get(match_map[rule], 0) else "red"
    return None
