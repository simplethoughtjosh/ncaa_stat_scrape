# Testing (keep features from breaking)

Every feature in this project should have an **automated test** that proves the requirement still works after future changes.

You do not need to be a developer to run the checks — only to interpret “pass” vs “fail.”

## Run all tests

From the project folder:

```bash
source .venv/bin/activate
pytest -q
```

A healthy result looks like: many dots, then `passed`.  
If something failed, the output names the test file and the requirement that broke.

## What we test (by area)

| Area | Test file(s) | Requirements covered |
|------|----------------|----------------------|
| Play parsers | `tests/test_events.py` | Player codes `H21`/`A14`; free-throw lines; sub in/out; timeouts; sub batching (hold unmatched same-team pairs, max 5) |
| Graphic payloads | `tests/test_payloads.py` | WBB/MBB sport config; player L3 stats; foul threshold 3 for both; leaders; sub slots; timeout fields; coach/season summary keys |
| Live ingest | `tests/test_live_ingest.py` | Play text + shot chart messages; FT auto-fill; sub auto queue; foul auto only on new entrants; foul hold from config; timeout auto on/off; manual cue |
| Team / coach season | `tests/test_team_season.py`, `tests/test_fetch_stats_html.py` | Linescore team page ids + W–L; team/coach HTML parsers; enrich soft-fail; game-summary coach/season vars; soft GET → curl gate clear → Chrome last resort on shared jar |
| Linescore totals | `tests/test_linescore_totals.py` | Prefer `score_{id}` cells; empty period / clock on one row still keeps the other team’s total |
| Scoreboard export | `tests/test_scoreboard_export.py` | Hex/filename helpers; color search ranking; skip existing Title_Stuff PNGs; generate only the missing side; Photoshop failure surfaces stderr |
| Shot maps | `tests/test_shot_map.py` | 3840×2160 PNG; home/away files; made shots for one org |
| Photos | `tests/test_player_photos.py`, `tests/test_photo_fetch.py` | SSC path matching; silhouette fallback; roster URL confirm / overlap |
| Control API | `tests/test_control.py` | `/status`, `/config` (incl. `foul_hold_seconds`), `/player`, foul/leaders take, `/auto` queue, graphic title overrides |
| Controller panel | `tests/test_controller_graphics.py`, `tests/test_controller_version.py` | Graphic dropdowns; foul-hold UI + storage key; auto playout queue; `VERSION` / HTML / XML / manifest sync |
| NewBlue config | `tests/test_newblue_config.py` | Example YAML points at STP controller; variable remap |
| CLI print helpers | `tests/test_cli_print.py` | On-court status print; live status line formatting |
| Bootstrap / session | `tests/test_live_bootstrap.py`, `tests/test_graphics.py`, `tests/test_ncaa_session.py`, `tests/test_access_gate.py`, `tests/test_live_snapshot.py` | Fixture HTML loads; dry-run Captivate pushes; cookie/gate/snapshot resume; stale zero-PTS detection; box-score keys with display-group suffix |

## Rule for new work

When you add or change a feature:

1. Write or update a test that states the **requirement** in the test name (e.g. `test_foul_trouble_mbb_threshold_of_three`).
2. Prefer a saved HTML fixture under `fixtures/` over hitting the live NCAA site in tests.
3. Run `pytest -q` before considering the work done.

## Dry-run vs live Captivate

Most tests use `transport: dry-run` so they never need Captivate running.  
They still prove we build the right variable payloads and call the client.

## Fixture game

`fixtures/wbb_live_box_score_6656891.html` is a captured women’s game (Western Colo. vs Chaminade). Tests reuse it so results stay stable.
