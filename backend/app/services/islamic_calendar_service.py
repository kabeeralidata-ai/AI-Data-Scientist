"""Approximate Gregorian dates for Ramadan, Eid al-Fitr, and Eid al-Adha — used as
calendar-event inputs for forecasting (a demand dip/shift during these dates should be
treated as a temporary, recurring calendar effect, not baked into the long-term trend).

Uses the mean synodic Islamic year length (354.36667 days) projected from a single,
well-established anchor date (1 Ramadan 1446 AH = 2025-03-01), rather than a fixed table
of hardcoded per-year dates — this is what makes it "computed per year" as the ~11-day
earlier shift each Gregorian year requires, instead of enumerating years by hand.

This is a deterministic ASTRONOMICAL APPROXIMATION, not a religious authority — real
moon-sighting-based dates can differ by 1-2 days from this estimate in either direction.
A multi-day buffer is applied wherever this is used as a "temporary dip" window, which
comfortably absorbs that uncertainty; this is never used for anything requiring
day-exact precision.
"""

from datetime import date, timedelta

HIJRI_MEAN_YEAR_DAYS = 354.36667
RAMADAN_ANCHOR_HIJRI_YEAR = 1446
RAMADAN_ANCHOR_GREGORIAN = date(2025, 3, 1)

# Offsets from 1 Ramadan to each event, in days — derived from the standard Islamic
# calendar's month lengths (~29.5 days/month): Eid al-Fitr is 1 Shawwal (~29-30 days
# after 1 Ramadan); Eid al-Adha is 10 Dhul Hijjah, three lunar months plus 9 days later
# (~98-99 days after 1 Ramadan). Cross-checked against the well-known 2025 dates (Ramadan
# start ~Mar 1, Eid al-Fitr ~Mar 31, Eid al-Adha ~Jun 6-7) — both land within a day.
RAMADAN_DURATION_DAYS = 29
EID_AL_FITR_OFFSET_DAYS = 29
EID_AL_ADHA_OFFSET_DAYS = 98


def ramadan_start(hijri_year: int) -> date:
    offset_years = hijri_year - RAMADAN_ANCHOR_HIJRI_YEAR
    days_offset = round(offset_years * HIJRI_MEAN_YEAR_DAYS)
    return RAMADAN_ANCHOR_GREGORIAN + timedelta(days=days_offset)


def _approx_hijri_year_for(d: date) -> int:
    days_since_anchor = (d - RAMADAN_ANCHOR_GREGORIAN).days
    return RAMADAN_ANCHOR_HIJRI_YEAR + round(days_since_anchor / HIJRI_MEAN_YEAR_DAYS)


def islamic_events(range_start: date, range_end: date, buffer_days: int = 3) -> list[dict]:
    """Returns every Ramadan / Eid al-Fitr / Eid al-Adha window (each padded by
    `buffer_days` on both sides) that overlaps [range_start, range_end]. Checks a couple
    of Hijri years on either side of the naive estimate so a range spanning a year
    boundary never misses an event near its edge."""
    events: list[dict] = []
    center_year = _approx_hijri_year_for(range_start)
    for hijri_year in range(center_year - 1, center_year + 3):
        r_start = ramadan_start(hijri_year)
        r_end = r_start + timedelta(days=RAMADAN_DURATION_DAYS)
        eid_fitr = r_start + timedelta(days=EID_AL_FITR_OFFSET_DAYS)
        eid_adha = r_start + timedelta(days=EID_AL_ADHA_OFFSET_DAYS)

        candidates = [
            ("Ramadan", r_start - timedelta(days=buffer_days), r_end + timedelta(days=buffer_days)),
            ("Eid al-Fitr", eid_fitr - timedelta(days=buffer_days), eid_fitr + timedelta(days=buffer_days)),
            ("Eid al-Adha", eid_adha - timedelta(days=buffer_days), eid_adha + timedelta(days=buffer_days)),
        ]
        for name, start, end in candidates:
            if start <= range_end and end >= range_start:
                events.append({"name": name, "start": start, "end": end, "hijri_year": hijri_year})

    events.sort(key=lambda e: e["start"])
    return events


def event_flag_for_date(d: date, events: list[dict]) -> str | None:
    for e in events:
        if e["start"] <= d <= e["end"]:
            return e["name"]
    return None
