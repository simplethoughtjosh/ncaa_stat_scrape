# Overview (plain language)

This project connects **live college basketball stats** from the NCAA website to **on-air graphics** in NewBlue Captivate / Titler Live.

Think of it as three cooperating parts:

```text
NCAA live box score  →  Stat scraper (this app)  →  NewBlue graphics
                              ↕
                     Operator control panel
```

## Who uses what

| Role | What they care about |
|------|----------------------|
| **Operator / producer** | Cue player bugs, take subs/timeouts/fouls, set foul hold time, turn Automatic Subs / Fouls on/off |
| **Graphics designer** | Link Captivate titles to `STP_NCAA_Basketball: Main` variables |
| **Engineer** | Keep the scraper running, fix parsers, add tests when features change |

## What a typical show looks like

1. Find today’s game (women’s or men’s) and start the scraper with that game id.
2. The scraper detects **Women’s (WBB)** vs **Men’s (MBB)** automatically from the box score.
3. Live stats stream in. The scraper keeps an up-to-date picture of the game (score, on-court five, fouls, etc.).
4. Captivate titles read variables from the **STP NCAA Basketball** controller.
5. You cue or auto-air graphics (player lower-third, substitutions, foul trouble, leaders, timeout, shot maps).

## Documents in this folder

| Doc | Read it if you want to… |
|-----|-------------------------|
| [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md) | Run a game today (step-by-step) |
| [STAT_SCRAPER.md](STAT_SCRAPER.md) | Understand where stats come from |
| [NEWBLUE_CONTROLLER.md](NEWBLUE_CONTROLLER.md) | Understand the Captivate panel & variables |
| [GRAPHICS_REFERENCE.md](GRAPHICS_REFERENCE.md) | See each graphic and what it shows |
| [TESTING.md](TESTING.md) | Know how we keep features from breaking |
| [VERSIONING.md](VERSIONING.md) | Controller version numbers (Major.Minor.Patch) |

## What we intentionally skip (for now)

- A dedicated “scoring play” lower-third that announces every basket with assist text. That can be added later.
