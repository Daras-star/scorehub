"""Sync layer: pulls matches from a data provider and upserts them into SQLite.

Providers all expose fetch_matches() and return a list of dicts shaped like:
    {
      "external_id": "abc123",
      "sport": "football" | "basketball",
      "league": "Premier League", "country": "England",
      "home": "Arsenal", "away": "Chelsea",
      "kickoff": "2026-10-09T19:00:00+00:00",
      "status": "scheduled" | "live" | "finished" | "postponed",
      "home_score": 1, "away_score": 0,
      "period": "2H", "clock": "63'",
    }

DemoProvider lets the site run end to end with no API key. To go real, write a
provider class for your chosen API that maps its response into this shape and
pass it to run_forever().
"""
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from config import load_env

load_env()  # reads FOOTBALL_DATA_KEY from the .env file, if there is one

DB_PATH = os.path.join(os.path.dirname(__file__), "scorehub.db")


class DemoProvider:
    """Generates fake fixtures around 'now' so the UI has something to show."""

    FIXTURES = [
        ("football", "Premier League", "England", "Arsenal", "Chelsea"),
        ("football", "Premier League", "England", "Liverpool", "Man City"),
        ("football", "La Liga", "Spain", "Real Madrid", "Barcelona"),
        ("football", "La Liga", "Spain", "Sevilla", "Valencia"),
        ("basketball", "NBA", "USA", "Lakers", "Celtics"),
        ("basketball", "NBA", "USA", "Warriors", "Bucks"),
    ]

    def __init__(self):
        self.seed = datetime.now(timezone.utc).strftime("%Y%m%d")

    def fetch_matches(self):
        now = datetime.now(timezone.utc)
        out = []
        for i, (sport, league, country, home, away) in enumerate(self.FIXTURES):
            rng = random.Random(f"{self.seed}-{i}")
            kickoff = now - timedelta(minutes=rng.choice([-90, -30, 20, 55, 130, 400]))
            elapsed = (now - kickoff).total_seconds() / 60
            length = 95 if sport == "football" else 150
            if elapsed < 0:
                status, hs, as_, period, clock = "scheduled", None, None, None, None
            elif elapsed > length:
                status = "finished"
                hs, as_ = self._score(rng, sport, 1.0)
                period, clock = "FT", None
            else:
                status = "live"
                hs, as_ = self._score(rng, sport, elapsed / length)
                period, clock = self._clock(sport, elapsed)
            out.append({
                "external_id": f"demo-{self.seed}-{i}",
                "sport": sport, "league": league, "country": country,
                "home": home, "away": away,
                "kickoff": kickoff.isoformat(), "status": status,
                "home_score": hs, "away_score": as_,
                "period": period, "clock": clock,
            })
        return out

    @staticmethod
    def _score(rng, sport, progress):
        top = 4 if sport == "football" else 120
        return (int(rng.random() * top * progress), int(rng.random() * top * progress))

    @staticmethod
    def _clock(sport, elapsed):
        if sport == "football":
            return ("1H" if elapsed <= 45 else "2H"), f"{min(int(elapsed), 90)}'"
        quarter = min(int(elapsed // 36) + 1, 4)
        return f"Q{quarter}", f"{12 - int(elapsed % 36 / 3):02d}:00"


class FootballDataProvider:
    """Real football fixtures and live scores from football-data.org (free tier).

    One request per poll covers every competition your key can see: Premier League,
    La Liga, Serie A, Bundesliga, Ligue 1, Champions League and a few more.
    The free plan allows 10 requests per minute, so keep the sync interval at 30s or more.
    """

    URL = "https://api.football-data.org/v4/matches"
    STATUS = {
        "SCHEDULED": "scheduled", "TIMED": "scheduled",
        "IN_PLAY": "live", "PAUSED": "live",
        "FINISHED": "finished", "AWARDED": "finished",
        "POSTPONED": "postponed", "SUSPENDED": "postponed", "CANCELLED": "postponed",
    }

    def __init__(self, api_key):
        self.headers = {"X-Auth-Token": api_key}

    def fetch_range(self, days_back, days_ahead, chunk_days=5):
        """Matches from days_back days ago to days_ahead days from now.

        Long ranges are split into short requests (APIs often cap the date span),
        with a one-day overlap so no day is missed. Duplicates are merged by id.
        """
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days_back)
        end = today + timedelta(days=days_ahead)
        found = {}
        while start <= end:
            stop = min(start + timedelta(days=chunk_days - 1), end)
            resp = requests.get(
                self.URL,
                headers=self.headers,
                params={"dateFrom": start.isoformat(), "dateTo": stop.isoformat()},
                timeout=15,
            )
            resp.raise_for_status()
            for m in resp.json().get("matches", []):
                found[m["id"]] = self._convert(m)
            if stop >= end:
                break
            start = stop  # overlap by one day
        return list(found.values())

    def fetch_matches(self):
        """Yesterday to tomorrow: the cheap request used for frequent live updates."""
        return self.fetch_range(1, 1)

    def _convert(self, m):
        status = self.STATUS.get(m.get("status"), "scheduled")
        minute = m.get("minute")
        period = clock = None
        if m.get("status") == "PAUSED":
            period = "HT"
        elif status == "live":
            period = "1H" if (minute or 0) <= 45 else "2H"
            clock = f"{minute}'" if minute else None
        elif status == "finished":
            period = "FT"

        full = (m.get("score") or {}).get("fullTime") or {}
        home_team, away_team = m["homeTeam"], m["awayTeam"]
        kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        return {
            "external_id": f"fd-{m['id']}",
            "sport": "football",
            "league": m["competition"]["name"],
            "country": (m.get("area") or {}).get("name"),
            "home": home_team.get("shortName") or home_team["name"],
            "away": away_team.get("shortName") or away_team["name"],
            "home_crest": home_team.get("crest"),
            "away_crest": away_team.get("crest"),
            "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
            "status": status,
            "home_score": full.get("home"),
            "away_score": full.get("away"),
            "period": period,
            "clock": clock,
        }


def upsert(conn, m):
    conn.execute(
        "INSERT OR IGNORE INTO leagues (sport, name, country) VALUES (?, ?, ?)",
        (m["sport"], m["league"], m.get("country")),
    )
    league_id = conn.execute(
        "SELECT id FROM leagues WHERE sport = ? AND name = ?", (m["sport"], m["league"])
    ).fetchone()[0]

    team_ids = []
    for name, crest in ((m["home"], m.get("home_crest")), (m["away"], m.get("away_crest"))):
        conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (name,))
        if crest:
            conn.execute("UPDATE teams SET crest = ? WHERE name = ?", (crest, name))
        team_ids.append(conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()[0])

    conn.execute(
        """
        INSERT INTO matches (external_id, league_id, home_id, away_id, kickoff, status,
                             home_score, away_score, period, clock, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_id) DO UPDATE SET
            kickoff=excluded.kickoff, status=excluded.status,
            home_score=excluded.home_score, away_score=excluded.away_score,
            period=excluded.period, clock=excluded.clock, updated_at=excluded.updated_at
        """,
        (m["external_id"], league_id, team_ids[0], team_ids[1], m["kickoff"], m["status"],
         m["home_score"], m["away_score"], m["period"], m["clock"],
         datetime.now(timezone.utc).isoformat()),
    )


def save_matches(matches):
    with sqlite3.connect(DB_PATH) as conn:
        for m in matches:
            upsert(conn, m)
    return len(matches)


def sync_once(provider):
    return save_matches(provider.fetch_matches())


def run_forever(provider, interval=30):
    while True:
        try:
            n = sync_once(provider)
            print(f"synced {n} matches")
        except Exception as exc:  # keep the worker alive on provider errors
            print("sync failed:", exc)
        time.sleep(interval)


def run_football(provider, interval=30, full_every=1800, days_back=7, days_ahead=7):
    """Live scores every `interval` seconds, plus a full -7/+7 day refresh every 30 minutes.

    Per minute this uses about 2 requests, plus about 4 more once every 30 minutes,
    well inside the free plan's 10 requests per minute.
    """
    last_full = 0.0
    while True:
        try:
            if time.time() - last_full >= full_every:
                n = save_matches(provider.fetch_range(days_back, days_ahead))
                last_full = time.time()
                print(f"full sync: {n} matches ({days_back} days back, {days_ahead} ahead)")
            else:
                print(f"live sync: {save_matches(provider.fetch_matches())} matches")
        except Exception as exc:  # keep the worker alive on provider errors
            print("sync failed:", exc)
        time.sleep(interval)


def clear_demo_data():
    """Remove the fake fixtures so only real matches are shown."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM matches WHERE external_id LIKE 'demo-%'")


if __name__ == "__main__":
    api_key = os.environ.get("FOOTBALL_DATA_KEY")
    if api_key:
        print("Using football-data.org (real scores)")
        clear_demo_data()
        run_football(FootballDataProvider(api_key))
    else:
        print("FOOTBALL_DATA_KEY not set, using demo data")
        run_forever(DemoProvider())