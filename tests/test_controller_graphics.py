"""The panel lets an operator pick a project graphic for each board."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "captivate_controller" / "STP_NCAA_Basketball"
HTML = ROOT / "STP_NCAA_BasketballMain.html"

GRAPHIC_KEYS = (
    "away_on_court",
    "home_on_court",
    "player_l3",
    "substitution",
    "foul_trouble",
    "leaders",
    "timeout",
    "game_summary",
    "shot_maps",
)


def test_each_purpose_has_a_graphic_dropdown():
    html = HTML.read_text()
    for key in GRAPHIC_KEYS:
        assert f'data-graphic="{key}"' in html
        assert f'id="graphic_{key}"' in html
    assert "getTitleControlInfo" in html
    assert "ServiceHandler.scheduler" in html
    assert "TITLE_REFRESH_MS" in html
    assert "setInterval(loadProjectTitles, TITLE_REFRESH_MS)" in html
    assert "pointerenter" in html
    assert "reopen this controller" not in html
    assert "Linked to this input" in html
    assert "stp_ncaa_graphic_" in html


def test_playout_reads_the_selected_graphic():
    html = HTML.read_text()
    assert "function selectedTitle(key)" in html
    assert "function autoTitleFor(kind)" in html
    assert "selectedTitle('substitution')" in html
    assert "selectedTitle('foul_trouble')" in html
    assert "selectedTitle('leaders')" in html
    assert "selectedTitle('timeout')" in html
    assert "autoFouls" in html
    assert "automatic_fouls" in html
    assert 'id="foulHoldSeconds"' in html
    assert "foul_hold_seconds" in html
    assert "FOUL_HOLD_STORAGE_KEY" in html
    assert "stp_ncaa_foul_hold_seconds" in html
    assert "function readFoulHoldSeconds()" in html
    assert "function persistFoulHoldSeconds(" in html
    assert "Foul hold (seconds)" in html
    assert "SUB_TITLE" not in html
    assert "TIMEOUT_TITLE" not in html


def test_foul_hold_is_pushed_with_config_and_restored_from_local_storage():
    """Operator foul hold must sync to the scraper and survive Titler reloads."""
    html = HTML.read_text()
    push = html[html.index("async function pushConfig()") : html.index("$('autoSubs').addEventListener")]
    assert "foul_hold_seconds: foulHold" in push
    assert "persistFoulHoldSeconds(foulHold)" in push
    assert "FOUL_HOLD_STORAGE_KEY" in html[html.index("// Restore local prefs") :]
    assert "$('foulHoldSeconds').addEventListener('change', pushConfig)" in html
    assert "$('foulHoldSeconds').addEventListener('blur', pushConfig)" in html


def test_animate_refuses_blank_graphic_selection():
    """Blank dropdown must not animateIn the whole input (airs every linked title)."""
    html = HTML.read_text()
    assert "needsTitle && !picked" in html
    assert "Select a graphic title before animate" in html
    assert "action === 'animateIn'" in html
    assert "action === 'animateOut'" in html
    assert "action === 'update'" in html


def test_later_sub_pairs_use_update_animation():
    html = HTML.read_text()
    assert "if (onAirKind === 'sub')" in html
    assert "subPairQueue = subPairQueue.concat(pairs)" in html
    start = html.index("if (subPairQueue.length)")
    chunk = html[start:html.index("await schedule('animateOut', selectedTitle('substitution')", start)]
    assert "schedule('update', selectedTitle('substitution'), next)" in chunk
    assert "animateIn" not in chunk
    assert "schedule('still'" not in chunk


def test_take_sub_uses_play_sub_pairs_not_empty_animate_in():
    html = HTML.read_text()
    start = html.index("async function takeSub()")
    chunk = html[start:html.index("async function takeFoul()", start)]
    assert "playSubPairs" in chunk
    assert "res.pairs" in chunk
    assert "schedule('animateIn', selectedTitle('substitution'), {})" not in chunk


def test_substitution_defaults_to_sub_graphic():
    html = HTML.read_text()
    assert "GRAPHIC_DEFAULTS" in html
    assert "substitution: 'sub'" in html
    assert "GRAPHIC_DEFAULTS[key]" in html


def test_graphic_select_uses_display_name_not_id():
    html = HTML.read_text()
    assert "function titleDisplayName(title)" in html
    assert "function resolveGraphicWant(key, saved, titles)" in html
    start = html.index("function fillGraphicSelect(sel, titles)")
    chunk = html[start : html.index("function restoreGraphicSelects()", start)]
    assert "opt.value = name" in chunk
    assert "title.id || title.name" not in chunk
    assert "localStorage.setItem(graphicStorageKey(key), name)" in html
    assert "localStorage.removeItem(graphicStorageKey(key))" in html


def test_auto_playout_queues_behind_on_air_kind():
    """Subs wait for foul hold/out instead of dismissing mid-hold."""
    html = HTML.read_text()
    assert "let pendingAuto = []" in html
    assert "async function drainPendingAuto()" in html
    assert "pendingAuto.push(action)" in html
    assert "await drainPendingAuto()" in html
    start = html.index("async function runAutoPlayout(action)")
    chunk = html[start : html.index("async function playSubPairs(", start)]
    assert "onAirKind && onAirKind !== kind && !force" in chunk
    assert "pendingAuto.push(action)" in chunk
    assert "dismissOnAir" not in chunk or "force" in chunk
    play = html[html.index("async function playSubPairs(") : html.index("function armSubHold(")]
    assert "if (onAirKind) await dismissOnAir()" not in play
    assert "onclick=\"clearFoul()\"" in html
    assert "pendingAuto = []" in html[html.index("async function clearFoul()") :]
    take = html[html.index("async function takeFoul()") : html.index("async function takeLeaders()")]
    assert "force: true" in take
