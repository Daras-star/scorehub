"""Loads settings from a .env file next to this script, so you don't have to set
environment variables in every terminal.

The .env file is plain text, one setting per line:

    FOOTBALL_DATA_KEY=paste-your-key-here

A variable already set in the terminal takes priority over the .env file.
"""
import os


def load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:  # utf-8-sig also copes with Notepad's BOM
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))