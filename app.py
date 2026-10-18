from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import Client, create_client

st.set_page_config(page_title="Warehouse Shift Hub", page_icon="🏭", layout="wide")

UK = ZoneInfo("Europe/London")
ROTATION_ANCHOR_MONDAY = date(2026, 10, 12)
BASE_COLOURS = {"Days": "Yellow", "Afters": "Red", "Nights": "Blue"}
COLOUR_HEX = {"Yellow": "#E0B323", "Red": "#D64545", "Blue": "#2878C8"}
SHIFT_ORDER = ["Days", "Afters", "Nights"]
SHIFT_LABELS = {"Days": "Days", "Afters": "Afters", "Nights": "Nights"}

DEFAULT_QUESTIONS = [
    {"question_key": "safety_trigger", "label": "Safety trigger", "section": "Safety & quality", "input_type": "choice", "rules": {"colour_rule": "severity_lmh"}, "options": ["L", "M", "H"], "sort_order": 10, "is_active": True, "is_system": True},
    {"question_key": "near_miss", "label": "Near miss / unsafe condition", "section": "Safety & quality", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 20, "is_active": True, "is_system": True},
    {"question_key": "sbos", "label": "SBOs", "section": "Safety & quality", "input_type": "number", "rules":