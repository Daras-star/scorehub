# ScoreHub

A small SofaScore-style live scores site for football and basketball, built with Flask and SQLite.

## Run it

```
pip install -r requirements.txt
python app.py          # terminal 1: web app at http://127.0.0.1:5000
python sync.py         # terminal 2: sync worker (demo data, refreshes every 30s)
```

## How it fits together

- `schema.sql`: leagues, teams and matches (one table for both sports, with `period` and `clock` fields).
- `sync.py`: a provider returns matches in a fixed shape and `upsert()` writes them to the database. `DemoProvider` generates fake fixtures so the site works without an API key.
- `app.py`: the page and a `/partial/matches` fragment that the page refreshes every 30 seconds.

## Going real

Write a provider class with a `fetch_matches()` method that calls your chosen API and returns dicts in the shape documented at the top of `sync.py`, then run `run_forever(YourProvider())`.
