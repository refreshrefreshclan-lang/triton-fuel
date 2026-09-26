# Triton Fuel

A personal nutrition tracker for UCSD dining halls, with official HDH nutrition facts.

- **`sync_menu.py`** reads every dining hall's menu and each item's nutrition page on hdh-web.ucsd.edu.
- **`.github/workflows/sync-menu.yml`** tells GitHub to run that script at 6 AM, 10 AM and 3 PM Pacific every day.
- **`docs/`** holds the results that GitHub Pages serves to everyone:
  - `docs/index.html`: the app, with the latest menu built in
  - `docs/menu.json`: the latest menu data on its own (for the future phone app)

Each run replaces `docs/` only if it succeeded. If UCSD's site is down or has changed, the run fails, GitHub emails you, and the app keeps showing the last good menu.

Run it on your own computer: `pip install -r requirements.txt`, then `python sync_menu.py`.
