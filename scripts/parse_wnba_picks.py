#!/usr/bin/env python3
"""
Parses a WNBA picks card (a wnba_*.txt upload) into data/wnba/tickets.json
for the WNBA tracker at /wnba/ -- the hidden page (2026-10-05: built in full,
deliberately not linked from the sport switch until the user unhides it).

A thin layer, like parse_combined_picks.py, and over the SAME machinery: the
card is read by parse_picks.parse() (every ticket / bettor / stake / payout
shape the group's generator has ever written), and every leg is resolved as
WNBA against data/wnba/roster.json -- players with their ESPN athlete id,
points, rebounds, assists, threes, steals, blocks, the combos, double- and
triple-doubles, and moneyline / spread / total. Re-implementing any of it here would only drift.

The slate is a DAY, like baseball's: its date is the picked teams' next game.
A card for a new day archives the old slate to tickets-previous.json; a
same-day re-upload replaces it.

Run:
    python scripts/parse_wnba_picks.py --file data/wnba/incoming_picks.txt
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_combined_picks as cp   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "wnba" / "tickets.json"
PREV_PATH = ROOT / "data" / "wnba" / "tickets-previous.json"


def build(text, now, fetcher=None, mlb=None, nfl=None, nhl=None, nba=None, wnba=None):
    """Every leg on the card as WNBA. Pure-ish, for the tests."""
    if mlb is None or nfl is None:
        mlb, nfl = cp.load_rosters()
    out = cp.build(text, mlb, nfl, now, fetcher, nhl=nhl, only_sport="wnba", nba=nba, wnba=wnba)
    out["sports"] = ["wnba"]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--prev", default=str(PREV_PATH))
    ap.add_argument("--no-network", action="store_true", help="don't ask ESPN for the WNBA schedule (offline tests)")
    args = ap.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    text = Path(args.file).read_text(encoding="utf-8")
    fetcher = (lambda _url: (_ for _ in ()).throw(RuntimeError("--no-network"))) if args.no_network else None
    out = build(text, now, fetcher)

    out_path, prev_path = Path(args.out), Path(args.prev)
    cp.archive_previous_slate(out["date"], out_path, prev_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
