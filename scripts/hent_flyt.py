#!/usr/bin/env python3
"""Hent fysisk utvekslingsflyt mellom norske prisområder og utlandet fra ENTSO-E.

Skriptet henter dokumenttype A11 (fysisk flyt over grensen) for hver retning
på hver utenlandsforbindelse, regner ut ukesnitt i MW (ISO-uke, norsk tid) og
skriver resultatet til docs/flows.json. Hentingen og flettingen med eksisterende
fil fungerer som i hent_priser.py.

Bruk:
    ENTSOE_TOKEN=... python3 scripts/hent_flyt.py          # oppdater siste år
    ENTSOE_TOKEN=... python3 scripts/hent_flyt.py --full   # hent alt fra 2015
"""

import argparse
import json
import os
import sys
from datetime import date, datetime

from hent_priser import AREAS, FIRST_YEAR, OSLO, monday_of_week_containing, update_series

OUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'docs', 'flows.json')

FOREIGN = {
    'SE1':   '10Y1001A1001A44P',
    'SE2':   '10Y1001A1001A45N',
    'SE3':   '10Y1001A1001A46L',
    'FI':    '10YFI-1--------U',
    'DK1':   '10YDK-1--------W',
    'NL':    '10YNL----------L',
    'DE-LU': '10Y1001A1001A82H',   # NordLink, fra desember 2020
    'GB':    '10YGB----------A',   # North Sea Link, fra oktober 2021
}

# (norsk område, utenlandsk område)
BORDERS = [
    ('NO1', 'SE3'),
    ('NO2', 'DK1'),
    ('NO2', 'NL'),
    ('NO2', 'DE-LU'),
    ('NO2', 'GB'),
    ('NO3', 'SE2'),
    ('NO4', 'SE1'),
    ('NO4', 'SE2'),
    ('NO4', 'FI'),
]

EIC = {**AREAS, **FOREIGN}


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

    existing = (None if args.full else load_existing()) or {}
    old = existing.get('serier', {})
    from_day = monday_of_week_containing(date(datetime.now(OSLO).year - 1, 1, 1))

    series = {}
    for no, foreign in BORDERS:
        for sender, receiver in ((no, foreign), (foreign, no)):
            name = f'{sender}>{receiver}'
            series[name] = update_series(
                token, name, [(EIC[receiver], date.min)], old.get(name), from_day,
                value_key='mw', required=False, document_type='A11', out_eic=EIC[sender],
            )

    if not any(series.values()):
        sys.exit('Fikk ingen flytdata; beholder eksisterende fil.')

    out = {
        'kilde': 'ENTSO-E Transparency Platform, fysisk flyt over grensen (A11)',
        'enhet': 'MW, ukesnitt',
        'forbindelser': [{'norsk': no, 'utland': foreign} for no, foreign in BORDERS],
        'serier': series,
    }
    tmp = OUT_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    os.replace(tmp, OUT_FILE)
    empty = [k for k, v in series.items() if not v]
    print(f'Skrev {sum(len(v) for v in series.values())} ukesnitt til {os.path.normpath(OUT_FILE)}')
    if empty:
        print(f'Uten data: {", ".join(empty)}')


if __name__ == '__main__':
    main()
