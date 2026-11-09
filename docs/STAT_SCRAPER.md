# Stat scraper

This is the Python app that talks to **stats.ncaa.org**, watches the live game, and prepares data for graphics.

You do **not** need to know Python to use it day-of — just start the `control` command (see [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md)). This page explains *what it does* in plain language.

## Where the data comes from

1. **Box score page** — loaded once when you open a game. Gives rosters, starting stats, and “secret keys” so we can listen live.
2. **Live stream (Faye)** — continuous updates: points, fouls, who’s on the court, play-by-play text, shot locations.

The scraper pretends to be a normal Chrome browser so the NCAA site accepts the connection. If the site still shows its access check (a short “verifying” page, then “Continue to NCAA Statistics?”), the scraper opens Chrome, reviews the terms page, and continues. That window closes by itself.

## Women’s vs men’s (automatic)

When the box score loads, the page already says whether the game is **WBB** (women’s) or **MBB** (men’s).

The scraper reads that and configures:

| Setting | Women’s (WBB) | Men’s (MBB) |
|---------|---------------|-------------|
| Period wording | Quarters | Halves |
| Foul trouble starts at | 3 personal fouls | 3 personal fouls |
| Foul out | 5 | 5 |

You should **not** have to flip a men’s/women’s switch in the graphics panel for a live game.

## What the scraper keeps in memory

For the selected game it always knows:

- Away / home team names and scores  
- Clock and period  
- Full roster with jersey, position, and common stats (PTS, FG, 3PT, FT, REB, AST, STL, BLK, TO, PF, minutes)  
- Which five are **on the court**  
- Recent play-by-play lines (subs, free throws, timeouts, …)  
- Shot chart dots (for the PNG maps)

## How play-by-play becomes graphics data

| Play text example | What we do |
|-------------------|------------|
| `Free throw 1 of 2 … by Jane Doe(ABC)` | Fill the **player lower-third** with Jane’s stats and `1 of 2` |
| `Substitution ABC: out …` / `in …` | Collect into an OUT list and an IN list (not “who replaced whom” — NCAA doesn’t say that) |
| `Timeout short ABC` / `Media Timeout` | Fill the **timeout** bumper fields |

Substitutions often arrive as several lines in a row. We wait a short beat, then publish one board with up to **5 OUT** and **5 IN**.

## Scoreboard team PNGs

On `control` start (and the `scoreboard` CLI / interactive command), the scraper calls:

`~/SSC Scoreboard Automation/.venv/bin/python ~/SSC Scoreboard Automation/export_scoreboard.py`

with home/away names and hex colors. **Before Photoshop runs**, each team is checked under:

`~/Documents/Title_Stuff/textures/2019 Scoreboards/Basketball/teams`

If that team’s PNG already exists (exact filename or a close name match such as `Penn St.` → `Penn St.png`), generation for that side is skipped. When both teams already exist, Photoshop is not started at all.

Colors (only for teams that still need a PNG) come from, in order: CLI override → `config/team_colors.yaml` → web search (`"{School} Athletics Hex colors"`, preferring teamcolorcodes-style pages) → dominant color from the school logo → fallback `#C8102E` / `#1A3A6B`. Successful web finds are cached under `~/.college_sports_scraper/team_color_cache.json`. Photoshop must be installed; failures print stderr (use `--require-scoreboard` to abort). Use `--skip-scoreboard` to leave the library alone for that session.

## Season records and coach summary

After the box score is live, the scraper looks up each school’s NCAA team page (and head-coach page when linked) and fills season W–L / conference records plus coach name, photo, school/career/season totals, and total seasons. Those values land on the **game summary** variables (`away_stats_*`, `home_coach_*`, …). Soft cookie fetch is preferred; Chrome opens only when the stats pages need it.

## Roster photos (optional)

If `roster-headshots` (`player-photos`) is installed and `~/SSC/<WBB|MBB>/<Team>/` is missing logos or most headshots, the scraper searches for the school’s Sidearm roster and downloads what it can (classic Sidearm cards and newer Nuxt / person-card layouts). Missing individual photos are skipped so one blank profile does not abort the roster.

## Local control port (`8765`)

While `control` is running, a tiny web service listens on your computer:

`http://127.0.0.1:8765`

The Captivate controller panel uses it to:

- Cue a player (`H21` / `A14`)  
- Toggle Automatic Subs / Automatic Foul Trouble / Automatic Timeouts  
- Set **foul hold seconds** (how long foul trouble stays on air)  
- Take foul trouble, leaders, timeout, sub  
- Read sport / score status  

Only works on the same machine (localhost). It is not exposed to the internet.

## Shot map images

The live box-score page embeds the full shot chart (the same `addShot(...)` calls the browser runs on reload). On startup the scraper reads those, then redraws two PNG files at **3840×2160** (2160p):

- `output/shot_maps/away_shot_map.png` / `home_shot_map.png` (stable “latest” copies)  
- `output/shot_maps/away_shot_map_<stamp>.png` / `home_shot_map_<stamp>.png` (paths pushed to Captivate)

Court background + dots for **makes only**, one team per file. New makes from Faye redraw the maps with a new stamp so Titler reloads the image; older stamped files are deleted so the folder stays small. A halftime restart still shows every make so far.

## Folder map (for the curious)

| Path | Role |
|------|------|
| `src/college_sports_scraper/ncaa/` | Talk to NCAA, live state |
| `src/college_sports_scraper/basketball/` | Build graphic payloads, parse plays, render shot maps |
| `src/college_sports_scraper/control/` | Local HTTP API + live orchestration |
| `src/college_sports_scraper/newblue/` | Send updates to Captivate |
| `fixtures/` | Saved HTML from a real game used in automated tests |
