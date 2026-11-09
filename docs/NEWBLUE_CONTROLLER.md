# NewBlue / Captivate controller

The **STP NCAA Basketball** controller is a small panel inside Captivate’s Data Controllers. Titles link to it so they always show the latest variables the scraper pushed.

The panel header shows a **version** (currently **v1.1.029**). It is listed under the **API** group, same as Kart Timing. How that number is bumped is explained in [VERSIONING.md](VERSIONING.md).

## Install (one-time)

1. Install the content pack `STP_NCAA_Basketball-Controller.nbpak` (Titler Live → Import Content Package). That pack is built from this folder and deploys to `${DATACONTROLLERS}/STP_NCAA_Basketball`, which on this Mac is:

```text
/Users/Shared/NewBlue/LiveEngine/DataControllers/STP_NCAA_Basketball
```

After that, day-to-day updates use `scripts/deploy_controller.sh` (run automatically whenever the agent changes the controller).

2. **Fully quit and relaunch Titler Live / Captivate** after any controller XML change (new variables). Captivate reads the controller XML **once at launch** — the version badge can update from HTML while Link Data still shows the old variable list until you restart.
3. In Titler / Captivate, open Data Controllers and confirm **STP_NCAA_Basketball: Main** appears.
4. On each graphic title, link text/image fields to the matching variable names (see [GRAPHICS_REFERENCE.md](GRAPHICS_REFERENCE.md)).

### When new variables appear for linking

Variables are declared in `STP_NCAA_BasketballMain.xml`. After deploy + Captivate restart they show under Link Data for **STP_NCAA_Basketball: Main**. At game start the scraper still-pushes `game_summary` (including season records and coach fields) so linked titles get values immediately.

## What the panel shows

### Status

- **Sport** — Women’s or Men’s (from the scraper; read-only)  
- **Score / clock** — quick glance  
- Whether the scraper on port **8765** is reachable  

### Configuration

| Option | When checked / set | When unchecked / notes |
|--------|--------------------|------------------------|
| **Automatic Subs** | Sub board fills → graphic animates on → holds ~4 seconds → animates off | Variables still update; you must press **Take Sub** |
| **Automatic Foul Trouble** | A *new* player hits the foul threshold → board animates on → holds → out | Variables still update; you press **Take Foul Trouble** |
| **Foul hold (seconds)** | Number of seconds the foul board stays up (1–60, default **4**) for both auto and Take | Editable mid-game; saved in the panel (`localStorage`) across Titler reloads |
| **Automatic Timeouts** | Same auto on-air behavior for timeout bumper | You press **Take Timeout** |

Checkbox prefs and foul hold are remembered in the panel and pushed to the scraper on change.

### Player L3 / Free throw

Type a short code and press **Cue Player**:

- `H21` = home team jersey 21  
- `A14` = away team jersey 14  

That fills the **same** player lower-third used for free throws. If the live play-by-play says someone is shooting free throws, the scraper fills this graphic automatically (including `1 of 2` style attempt text).

### Substitutions

- Preview of the latest OUT / IN counts  
- **Take Sub** — put the board on air (and run the timed out if the panel handles animate)  
- **Clear Sub** — take it off immediately  

### Boards

Buttons to push / take:

- Foul trouble  
- Leaders (PTS / REB / AST)  
- Timeout  

## How Captivate receives data

Two paths work together:

1. **Scraper → Captivate Advanced API** (ports 9022 HTTP or 9025 TCP)  
   Pushes variable updates as the game changes (`still` = update without fancy animation).

2. **Panel → scraper (`8765`)**  
   Operator actions (cue player, config, take).

3. **Panel → Captivate `scheduleAction`**  
   `animateIn` / `animateOut` / `update` for the graphic chosen in that section’s dropdown. Automatic Subs and Automatic Timeouts use the same picks.

## Config file for engineers

On the panel, each board has a **Graphic** dropdown filled from the open project. The panel asks Captivate again about every two seconds, so a title added while Titler is open appears in the list. That choice is what Take, Clear, and live still-updates use. `config/newblue_titles.yaml` (copy from the `.example` file) is only the fallback when a dropdown is left on **Linked to this input**. It also tells the scraper:

- `transport: dry-run` — print what *would* be sent (safe for testing)  
- `transport: http` — talk to a real Captivate on this machine  
- Which Captivate **input** name to use (`STP_NCAA_Basketball: Main`)  
- Optional remaps from logical field names → your title variable names  

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| Panel says scraper offline | Start `python -m college_sports_scraper.cli control --game-id …` |
| Variables never change on air | Set `transport: http` and confirm Captivate Advanced API is enabled |
| Wrong sport badge | Re-open the correct game id; sport comes from that box score |
| Player cue “unknown” | Jersey not on roster / wrong H vs A |
