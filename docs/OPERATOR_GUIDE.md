# Operator guide (day of show)

Follow these steps in order. You do not need to edit code.

## Before the game (once per machine)

1. Confirm Captivate / Titler Live is installed and the **STP_NCAA_Basketball** controller was copied into DataControllers (see [NEWBLUE_CONTROLLER.md](NEWBLUE_CONTROLLER.md)).
2. Open a terminal in this project folder.
3. Activate the environment and confirm dependencies:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

4. Make sure `config/newblue_titles.yaml` exists (copy from the example if needed) and set:

```yaml
transport: http
```

when you are ready to drive real graphics (`dry-run` only prints messages — good for rehearsal without Captivate).

## Start of game

### 1. Find the game id (optional)

```bash
python -m college_sports_scraper.cli find-games --team "Your Team" --sport WBB --date MM/DD/YYYY
```

Use `MBB` for men’s. Note the **game id** number from the list (or paste it from the NCAA live box-score URL).

### 2. Start the live controller

```bash
python -m college_sports_scraper.cli control --game-id YOUR_GAME_ID
```

The first time you open a game, a Chrome window may appear, open the NCAA terms page, and close on its own. That is the site asking you to confirm the terms before it will show the live box score. A restart reuses cookies when they still work, and restores the last live snapshot (period, clock, score, shots, lineups) saved on quit — NCAA’s stream has no replay “sequence,” so that snapshot is how we catch up like a page reload. Chrome opens again only when the saved session cannot subscribe or there is no snapshot yet. Leave the terminal window open. You should see:

- Sport auto-detected (`WBB` or `MBB`)  
- Control HTTP on port **8765**  
- Faye connecting  
- Scoreboard team PNGs via **SSC Scoreboard Automation** (Photoshop) into `Documents/Title_Stuff/textures/2019 Scoreboards/Basketball/teams`. Teams that already have a PNG there are **skipped**; only missing schools are generated. Disable the whole step with `--skip-scoreboard`, or type `scoreboard` later. Colors (for new teams only): CLI / `team_colors.yaml` / web search (`School Athletics Hex colors`) / logo sample.
- Season W–L / coach summary fields enrich in the background after connect (for the team-summary graphic).
- Incomplete `~/SSC` headshots/logos may auto-download from the athletics roster when `player-photos` is installed.


### 3. Open Captivate

- Confirm the STP NCAA Basketball panel shows **Scraper OK** and the correct sport badge (version **v1.1.029** or newer for foul-hold control).
- On each section, open **Graphic** and choose the title from this project (away five, home five, player lower-third, subs, foul trouble, leaders, timeout, team summary, shot maps). The list refreshes about every two seconds, and again when you point at a dropdown, so a title you add stays available without restarting Titler. **Automatic Subs / Fouls / Timeouts require a named title** in that dropdown — leaving **Linked to this input** blank refuses animate so every linked graphic does not take at once.
- **Automatic Subs** and **Automatic Foul Trouble** start on. Turn either off to Take yourself. **Automatic Timeouts** stays off until you enable it.
- Set **Foul hold (seconds)** under Configuration (default 4). Change it anytime during the game; the value is saved on this machine and used for both auto foul trouble and **Take Foul Trouble**.

## During the game

| Need | What to do |
|------|------------|
| Player lower-third / FT pre-cue | Type `H21` or `A14` → **Cue Player**, then take the player title in Captivate |
| Free throw already happening | Scraper fills the same player fields automatically — just take the title if it isn’t auto-tied |
| Substitution graphic | With Automatic Subs off: wait for preview → **Take Sub**. With it on, after the clock leaves the period’s opening time: same-team outs/ins are held until they pair (or ~8s if a match never arrives), then the board animates on, shows each out/in pair for about 4 seconds, then animates off |
| Foul trouble | Auto-airs when someone newly hits the threshold (≥3 PF); or **Take Foul Trouble**. Hold length = **Foul hold (seconds)** on the panel |
| Leaders | **Take Leaders** |
| Timeout | **Take Timeout**, or enable Automatic Timeouts |
| Shot maps | Paths update in the background; take the title that uses those images |

## Keyboard / terminal shortcuts (optional)

In the `control` window:

| Type | Result |
|------|--------|
| `status` | Print on-court fives |
| `player H21` | Same as Cue Player |
| `summary` | Push game summary vars |
| `foul` / `leaders` | Push those boards |
| `sub` or `sub 2` | Fake a substitution with random roster names (airs the graphic) |
| `quit` | Stop cleanly |

A roster photo that shows up after startup replaces the silhouette within about 5 seconds, and `both` sends the lineup immediately.

## End of game

1. Type `quit` in the control window (or Ctrl+C).  
2. You can leave Captivate open for the next game; start `control` again with the new game id.

## If something’s wrong

1. Panel says scraper offline → control window isn’t running or crashed; restart step 2.  
2. NCAA / Faye errors → network or game not actually live; try again or use a known live id.  
3. Graphics blank → `transport` still `dry-run`, or titles not linked to `STP_NCAA_Basketball: Main`.  
4. Wrong players on FT → cue manually with `H`/`A` + jersey; auto only fires after the free-throw play line appears.
