# Graphics reference

Each graphic below is fed by variables on **STP_NCAA_Basketball: Main**. Designers bind title fields to these names. Operators only need to know *when* to take each graphic.

## On-court five (away / home)

**Purpose:** Show the five players currently on the floor for one team.

**Updates:** Automatically whenever who is on the floor changes — from the box-score Active column and from substitution play-by-play lines — so this graphic always matches the current five, including mass subs at the start of a period.

**Main variables:** `away_team` / `home_team`, then for each spot 1–5: `away_p1_name`, `away_p1_name_first`, `away_p1_name_last`, `away_p1_number`, `away_p1_pos`, `away_p1_photo` (and the same `home_p1_*` … `home_p5_*` fields).

`away_p1_name` is the complete name. `away_p1_name_first` is the first word. `away_p1_name_last` is every word after the first, so “Maris Kate Payseur” is Maris / Kate Payseur and “Ryan van Eyck” is Ryan / van Eyck. A one-word name fills the first-name field and leaves the last-name field blank.

**Photos** are image variables. The scraper fills each one with the file at `~/SSC/<WBB or MBB>/<Team Name>/<player name>.jpg`. When that file is missing, the same variable gets a silhouette so the frame is never empty: a female headshot for women's basketball and Titler's male headshot for men's basketball. A photo downloaded after the scraper is already running replaces that silhouette within about 5 seconds, and typing `both` sends the lineup immediately. Link the away title to `away_p1_photo` … `away_p5_photo`, and the home title to `home_p1_photo` … `home_p5_photo`.

---

## Player lower-third (also free throws)

**Purpose:** One player’s popular game stats. Used for:

- Manual look-ins (`H21` / `A14`)  
- Free-throw situations (auto-filled from play-by-play)

**Stats included:** PTS, FG, 3PT, FT (as made-attempt), REB, AST, STL, BLK, TO, PF, MIN, plus optional `player_ft_attempt` (e.g. `1 of 2`).

**Main variables:** `player_team`, `player_name`, `player_number`, `player_pos`, `player_photo` (image from `~/SSC/<WBB|MBB>/<Team>/<player>.jpg`, or silhouette), `player_team_logo` (image from `~/SSC/<WBB|MBB>/<Team>/logo.png`), `player_pts`, `player_fg`, `player_3pt`, `player_ft`, `player_reb`, `player_ast`, `player_stl`, `player_blk`, `player_to`, `player_pf`, `player_min`, `player_ft_attempt`, `player_code`.

---

## Game summary

**Purpose:** Side-by-side team totals with score and clock.

**Updates:** When score / clock / team totals change.

**Main variables:** `away_team`, `home_team`, `away_team_logo` / `home_team_logo` (image from `~/SSC/<WBB|MBB>/<Team>/logo.png`), `away_score`, `home_score`, `period_label`, `clock`, shooting and counting lines (`away_fg` / `home_fg`, `away_reb` / `home_reb`, `away_ast` / `home_ast`, `away_stl` / `home_stl`, `away_blk` / `home_blk`, `away_to` / `home_to`, `away_pf` / `home_pf`, …). Season-to-date records: `away_stats_overall`, `away_stats_conference`, `home_stats_overall`, `home_stats_conference`. Head coach summary: `away_coach_name` / `home_coach_name`, `away_coach_photo` / `home_coach_photo` (image from `~/SSC/<WBB|MBB>/<Team>/<coach>.jpg`, or silhouette), plus `*_coach_school_total`, `*_coach_career_total`, `*_coach_season_total`, `*_coach_total_seasons`. Also includes sport fields (`sport_code`, …).

These coach/season names are declared on the controller XML and still-pushed with game summary at startup. If Link Data does not list a new name after a controller update, fully quit and relaunch Captivate (XML is loaded once at launch — see [NEWBLUE_CONTROLLER.md](NEWBLUE_CONTROLLER.md)).

---

## Substitution board (OUT | IN)

**Purpose:** Leaderboard-style list of who left and who entered. NCAA does **not** say who replaced whom, so we show two columns.

**Capacity:** One out and one in at a time (FIFO pairing when several leave and enter).

**Air behavior:**

- Automatic Subs **on**, and the clock has moved off the period’s opening time (10:00 women’s, 20:00 men’s, 5:00 overtime) → animate on, show each out/in pair for about 4 seconds, play the update animation for both names (and the team logo when the other side also substitutes) between pairs, then animate off. It does not air at the start of a period.
- **off** → operator presses Take Sub  

**Main variables:** `sub_team`, `sub_team_logo` (image from `~/SSC/<WBB or MBB>/<Team Name>/logo.png`), `sub_out`, `sub_in` (player names).

---

## Foul trouble

**Purpose:** List up to five players at or above the foul-trouble threshold (both teams).

**Rules:** ≥ 3 PF for both WBB and MBB. Fouled-out players (5 PF) show `OUT` and sort to the top, then most fouls → least.

**Air behavior:** **Automatic Foul Trouble** (default on) animates the board when a *new* player reaches the threshold, holds for the controller **Foul hold (seconds)** setting (default 4s; editable mid-game and saved on the panel), then animates out. Variables still update live so Take (or an on-air update) always shows everyone currently in trouble. PF bumps for players already on the board do not re-trigger auto-air.

**Link per slot (1–5):** `foul_N_logo` (image), `foul_N_name`, `foul_N_pf`. Optional: `foul_N_team`, `foul_N_number`. Board-level: `foul_count`.

---

## Game leaders

**Purpose:** Top 3 in points, rebounds, and assists (both teams combined).

**Air behavior:** Manual Take.

**Main variables:** `lead_pts_1_name`…`lead_pts_3_value`, same pattern for `lead_reb_*` and `lead_ast_*`.

---

## Timeout bumper

**Purpose:** Full-screen or lower bumper for team timeout / media timeout.

**Air behavior:** Automatic Timeouts checkbox, or Take Timeout.

**Main variables:** `timeout_label`, `timeout_team`, `timeout_clock`, `timeout_period`.

---

## Shot maps (images)

**Purpose:** Full-court made-shot chart PNGs at 2160p.

**Image variables:** `away_shot_map`, `home_shot_map` (seeded from the box-score page on start, then updated when new makes arrive).

Each rebuild writes a **new versioned path** (and prunes older ones) so Captivate reloads the texture without a manual Recache. Stable `away_shot_map.png` / `home_shot_map.png` copies stay in `output/shot_maps/` for browsing.

Each map places that team’s `logo.png` at center court **under** the make dots. Link an image object in your title to those names on `STP_NCAA_Basketball: Main`, same as player photos. NCAA tags each make with the school org id from the logo URL; the scraper matches that to away/home when drawing the dots.

---

## Sport badge (config)

**Purpose:** Let titles or the panel know women’s vs men’s.

**Variables:** `sport_code` (`WBB`/`MBB`), `sport_label`, `is_wbb`, `period_unit`, `foul_trouble_threshold`.
