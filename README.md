# College Sports Scraper → NewBlue Graphics

Live **NCAA college basketball** stats driving **NewBlue Captivate / Titler** graphics.

**Start here if you are not an engineer:** open **[docs/OVERVIEW.md](docs/OVERVIEW.md)**, then the [Operator guide](docs/OPERATOR_GUIDE.md).

## Documentation map

| Document | Audience | What’s inside |
|----------|----------|----------------|
| [docs/OVERVIEW.md](docs/OVERVIEW.md) | Everyone | Big picture in plain language |
| [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) | Producers / operators | Day-of-show steps |
| [docs/STAT_SCRAPER.md](docs/STAT_SCRAPER.md) | Operators + engineers | How NCAA data is collected |
| [docs/NEWBLUE_CONTROLLER.md](docs/NEWBLUE_CONTROLLER.md) | Operators + designers | Captivate panel, install, troubleshooting |
| [docs/GRAPHICS_REFERENCE.md](docs/GRAPHICS_REFERENCE.md) | Designers + operators | Each graphic and its variables |
| [docs/TESTING.md](docs/TESTING.md) | Engineers (+ curious ops) | How we keep features from breaking |
| [docs/VERSIONING.md](docs/VERSIONING.md) | Everyone shipping changes | Controller `Major.Minor.Patch` rules |

## Quick start (technical)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Auto headshots/logo at connect (sibling repo):
pip install -e "../player-photos"
# or: pip install -e ".[photos]"
cp config/newblue_titles.example.yaml config/newblue_titles.yaml
# Set transport: http when Captivate is running

# Install captivate_controller/STP_NCAA_Basketball/STP_NCAA_Basketball-Controller.nbpak
# via Titler Live → Import Content Package, then restart Titler Live

python -m college_sports_scraper.cli control --game-id YOUR_GAME_ID
```

Find a game id:

```bash
python -m college_sports_scraper.cli find-games --team Chaminade --sport WBB --date 11/01/2026
```

## What ships today

- Auto **WBB / MBB** detection from the box score  
- On-court fives, game summary (incl. season W–L + coach fields when NCAA pages enrich)  
- **Player L3** = free-throw graphic (manual `H21`/`A14` + auto on FT plays)  
- Substitution OUT\|IN board with optional Automatic Subs  
- Foul trouble (auto on new entrants; operator **Foul hold** seconds on the panel), leaders, timeout bumper  
- Scoreboard team PNGs via Photoshop — **skips schools that already exist** in the Title_Stuff teams library  
- Optional Sidearm roster photo download when `player-photos` is installed  
- **2160p** home/away made-shot PNGs  
- Captivate controller **STP_NCAA_Basketball: Main** (v1.1.029+) + local control API `:8765`

## Verify nothing broke

```bash
pytest -q
```

See [docs/TESTING.md](docs/TESTING.md). New features should add tests that encode the stated requirements.
