#!/usr/bin/env python3
"""Hent day-ahead-områdepriser for NO1–NO5 fra ENTSO-E og lagre ukesnitt.

Skriptet henter dokumenttype A44 (day-ahead-priser) fra ENTSO-E Transparency
Platform, regner ut tidsvektet snitt per ISO-uke (norsk tid, samme ukeinndeling
som NVEs magasinstatistikk) og skriver resultatet til docs/prices.json.

Bruk:
    ENTSOE_TOKEN=... python3 scripts/hent_priser.py          # oppdater siste år
    ENTSOE_TOKEN=... python3 scripts/hent_priser.py --full   # hent alt fra 2015

Uten --full hentes bare fra starten av fjoråret, og eldre uker beholdes fra
eksisterende fil. Finnes ikke fila, hentes alt.
"""

import argparse
import bisect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_URL = 'https://web-api.tp.entsoe.eu/api'
OSLO = ZoneInfo('Europe/Oslo')
FIRST_YEAR = 2015
OUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'docs', 'prices.json')

AREAS = {
    'NO1': '10YNO-1--------2',
    'NO2': '10YNO-2--------T',
    'NO3': '10YNO-3--------J',
    'NO4': '10YNO-4--------9',
    'NO5': '10Y1001A1001A48H',
}


# ── HTTP ─────────────────────────────────────────────────────
def fetch_xml(token, eic, start, end):
    """Hent rå XML for ett område og én periode (maks ett år)."""
    params = {
        'securityToken': token,
        'documentType': 'A44',
        'in_Domain': eic,
        'out_Domain': eic,
        'periodStart': start.strftime('%Y%m%d%H%M'),
        'periodEnd': end.strftime('%Y%m%d%H%M'),
    }
    url = f'{API_URL}?{urllib.parse.urlencode(params)}'
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            # «Ingen data» kommer som et Acknowledgement-dokument, ofte med HTTP 400
            if b'Acknowledgement_MarketDocument' in body:
                return body
            if e.code < 500 or attempt == 3:
                raise RuntimeError(f'ENTSO-E svarte HTTP {e.code}: {body[:300]!r}') from None
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt + 1))


# ── XML-parsing ──────────────────────────────────────────────
def local(tag):
    return tag.rsplit('}', 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def parse_time(s):
    # Format: 2025-10-01T22:00Z
    return datetime.strptime(s, '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc)


def parse_resolution(s):
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?', s)
    if not m or not (m.group(1) or m.group(2)):
        raise ValueError(f'Ukjent oppløsning: {s}')
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def parse_periods(xml_bytes):
    """Returner liste av (start, slutt, minutter per steg, [priser]).

    Posisjoner som mangler (kurvetype A03) fylles med forrige pris.
    """
    root = ET.fromstring(xml_bytes)
    if local(root.tag) == 'Acknowledgement_MarketDocument':
        reason = root.find('.//{*}Reason/{*}text')
        text = reason.text if reason is not None else ''
        if 'No matching data' in text:
            return []
        raise RuntimeError(f'ENTSO-E avviste forespørselen: {text}')

    periods = []
    for ts in children(root, 'TimeSeries'):
        # Enkelte svar har flere serier for samme område; bruk bare den primære.
        seq = child(ts, 'classificationSequence_AttributeInstanceComponent.position')
        if seq is not None and seq.text.strip() != '1':
            continue
        for period in children(ts, 'Period'):
            interval = child(period, 'timeInterval')
            start = parse_time(child(interval, 'start').text)
            end = parse_time(child(interval, 'end').text)
            res = parse_resolution(child(period, 'resolution').text)
            n = int((end - start).total_seconds() // 60 // res)
            by_pos = {}
            for p in children(period, 'Point'):
                by_pos[int(child(p, 'position').text)] = float(child(p, 'price.amount').text)
            prices, last = [], None
            for pos in range(1, n + 1):
                last = by_pos.get(pos, last)
                prices.append(last)
            periods.append((start, end, res, prices))
    return periods


def drop_overlaps(periods):
    """Fjern perioder som overlapper; finest oppløsning vinner."""
    accepted_starts, accepted = [], []
    for p in sorted(periods, key=lambda p: (p[2], p[0])):
        start, end = p[0], p[1]
        i = bisect.bisect_left(accepted_starts, start)
        if i > 0 and accepted[i - 1][1] > start:
            continue
        if i < len(accepted) and accepted[i][0] < end:
            continue
        accepted_starts.insert(i, start)
        accepted.insert(i, p)
    return accepted


# ── Aggregering ──────────────────────────────────────────────
def weekly_averages(periods):
    """Tidsvektet snitt per ISO-uke i norsk tid → {(år, uke): (snitt, timer)}."""
    sums = {}
    for start, _end, res, prices in drop_overlaps(periods):
        step = timedelta(minutes=res)
        for i, price in enumerate(prices):
            if price is None:
                continue
            iso = (start + i * step).astimezone(OSLO).isocalendar()
            key = (iso[0], iso[1])
            s = sums.setdefault(key, [0.0, 0])
            s[0] += price * res
            s[1] += res
    return {k: (s[0] / s[1], s[1] / 60) for k, s in sums.items() if s[1]}


def monday_of_week_containing(d):
    return d - timedelta(days=d.weekday())


def fetch_area(token, eic, from_day):
    start = datetime.combine(from_day, datetime.min.time(), OSLO).astimezone(timezone.utc)
    stop = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=2)
    periods = []
    while start < stop:
        end = min(start + timedelta(days=365), stop)
        periods += parse_periods(fetch_xml(token, eic, start, end))
        start = end
    return weekly_averages(periods)


# ── Hovedprogram ─────────────────────────────────────────────
def load_existing():
    try:
        with open(OUT_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--full', action='store_true', help=f'hent alt fra {FIRST_YEAR}')
    args = ap.parse_args()

    token = os.environ.get('ENTSOE_TOKEN', '').strip()
    if not token:
        sys.exit('Mangler miljøvariabelen ENTSOE_TOKEN.')

    existing = None if args.full else load_existing()
    first_year = FIRST_YEAR if existing is None else datetime.now(OSLO).year - 1
    from_day = monday_of_week_containing(date(first_year, 1, 1))
    from_key = tuple(from_day.isocalendar()[:2])

    areas = {}
    for name, eic in AREAS.items():
        print(f'Henter {name} fra {from_day}…', flush=True)
        weeks = fetch_area(token, eic, from_day)
        if not weeks:
            sys.exit(f'Fikk ingen priser for {name}; beholder eksisterende fil.')
        if existing is not None:
            for r in existing['omrader'].get(name, []):
                key = (r['iso_aar'], r['iso_uke'])
                if key < from_key:
                    weeks.setdefault(key, (r['pris'], r['timer']))
        areas[name] = [
            {'iso_aar': y, 'iso_uke': w, 'pris': round(p, 2), 'timer': round(h, 2)}
            for (y, w), (p, h) in sorted(weeks.items())
        ]

    out = {
        'kilde': 'ENTSO-E Transparency Platform, day-ahead-priser (A44)',
        'enhet': 'EUR/MWh',
        'omrader': areas,
    }
    tmp = OUT_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    os.replace(tmp, OUT_FILE)
    print(f'Skrev {sum(len(v) for v in areas.values())} ukesnitt til {os.path.normpath(OUT_FILE)}')


if __name__ == '__main__':
    main()
