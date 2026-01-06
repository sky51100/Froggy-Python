from __future__ import annotations
from Py4GWCoreLib import (Routines,Botting,ActionQueueManager)
import os
import time
from typing import Generator, List, Tuple, Optional

import PyImGui as ImGui
from Py4GWCoreLib import *
from Widgets.CustomBehaviors.gui import party

MODULE_NAME = "Farm_Froggy"

# ---------------------------------------------------------------------------
# Constants (from your AutoIt addon)
# ---------------------------------------------------------------------------
MAP_GADDS_ENCAMPMENT = 638
MAP_SPLARKFLY = 558
MAP_BOGROOT_L1 = 615
MAP_BOGROOT_L2 = 616
MAP_GTOB = 248

DWARVEN_BLESSING_DIALOG = 0x84

SUMMONING_STONE_MODELS = [
    37810, 30209, 30210, 35126, 31156, 32557, 31155, 30960, 30963, 34176,
    30961, 30966, 30846, 30965, 30959, 30964, 30962, 31022, 31023
]

CONSET_MODELS = [24859, 24860, 24861]  # Essence, Armor, Grail
EFFECT_ESSENCE = 2522
EFFECT_ARMOR = 2520
EFFECT_GRAIL = 2521

FROGGY_SCEPTER_MODELS = [
    1197,
    1556,
    1569,
    1439,
    1563,
]


# ---------------------------------------------------------------------------
# Waypoints
# ---------------------------------------------------------------------------



BOSS_DOOR_POS = (17922.0, -6241.0)
CHEST_POS = (14982.66, -19122.0)
TEKKS_POS = (14497.64, -17438.0)

# ---------------------------------------------------------------------------
# Runtime stats / UI state
# ---------------------------------------------------------------------------
class _Stats:
    def __init__(self):
        self.session_start = time.time()
        self.current_run_start: Optional[float] = None
        self.run_count = 0
        self.success = 0
        self.fail = 0
        self.last_run_s: Optional[int] = None
        self.fastest_s: Optional[int] = None
        self.total_s = 0
        self.froggy_total = 0

STATS = _Stats()

class _Settings:
    def __init__(self):
        self.hard_mode = False
        self.use_summon_stage1 = False
        self.use_summon_stage2 = False
        self.use_conset_stage1 = False
        self.use_conset_stage2 = False

SET = _Settings()

# ---------------------------------------------------------------------------
# Death / wipe handling (Qinkai-style)
# ---------------------------------------------------------------------------
RESPAWN_MAX_DIST = 2000  # tolérance max (à ajuster si besoin)


RESPAWN_POINTS = {
    "First Level": [
        (19045.95, 7877),    # DoStep 2
        (5083, 2155),        # DoStep 19
        (-1547, -8696),      # DoStep 30
    ],
    "Second Stage": [
        (-11055, -5551),     # DoStep 42
        (-955, 10984),       # DoStep 63
        (8591, 4285),        # DoStep 75
        (19619, -11498),     # DoStep 94
    ],
}


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def FindBestRecoveryState() -> str | None:
    try:
        x, y = GLOBAL_CACHE.Player.GetXY()
    except Exception:
        return None

    best_state = None
    best_dist = float("inf")

    for state_name, points in RESPAWN_POINTS.items():
        for px, py in points:
            d = _dist((x, y), (px, py))
            if d < best_dist:
                best_dist = d
                best_state = state_name

    if best_dist <= RESPAWN_MAX_DIST:
        return best_state

    return None


def _coro_on_party_wipe(bot: "Botting"):
    fsm = bot.config.FSM

    # attendre résurrection
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot.Wait._coro_for_time(1000)

        if not Routines.Checks.Map.MapValid():
            try:
                fsm.resume()
            except Exception:
                pass
            return

    # stabilisation post-rez
    yield from bot.Wait._coro_for_time(800)

    # suppression DP
    RemoveDeathPenaltyIfAny()
    yield from bot.Wait._coro_for_time(500)

    # 🔴 POINT CRITIQUE : reset mouvement
    try:
        ActionQueueManager().ResetAllQueues()

    except Exception:
        pass

    # reprise intelligente
    state = FindBestRecoveryState()

    try:
        if state:
            print(f"[RECOVER] Respawn → jump to '{state}'")
            fsm.jump_to_state_by_name(state)
        else:
            print("[RECOVER] Respawn detected but no valid recovery state")

        fsm.resume()
    except Exception as e:
        print(f"[RECOVER] FSM error: {e}")





def OnPartyWipe(bot: "Botting"):
    fsm = bot.config.FSM

    if getattr(bot, "_handling_wipe", False):
        return

    bot._handling_wipe = True

    try:
        fsm.pause()
    except Exception:
        pass

    def _resume_wrapper():
        yield from _coro_on_party_wipe(bot)
        bot._handling_wipe = False

    bot.States.AddManagedCoroutine("OnWipe_Resume", _resume_wrapper)



DP_REMOVAL_MODELS = [
    6370,   # Peppermint CC
    19039,  # Refined Jelly
    21227,  # Elixir of Valor
    21488,  # Wintergreen CC
    21489,  # Rainbow CC
    22191,  # Four Leaf Clover
    26784,  # Honeycomb
    28433,  # Pumpkin Cookie
    30206,  # Oath of Purity
    30211,  # Seal of the Dragon Empire
    35127,  # Shining Blade Ration
]


def RemoveDeathPenaltyIfAny() -> bool:
    try:
        agent_id = Player.GetAgentID()
        dp = Agent.GetDeathPenalty(agent_id)
    except Exception:
        return False

    if dp <= 0:
        return False

    for model_id in DP_REMOVAL_MODELS:
        item_id = Item.GetItemIdFromModelID(model_id)
        if item_id:
            Inventory.UseItem(item_id)
            print(f"🧹 Death Penalty removed ({dp}%) using item {model_id}")
            return True

    print(f"⚠️ Death Penalty detected ({dp}%) but no DP-removal item available")
    return False



def _take_or_retake_quest(bot: Botting) -> Generator:
    bot.Move.XYAndDialog(12503.0, 22721.0, 0x833901)
    yield from Routines.Yield.wait(800)
    yield

def _wait_end_dungeon() -> Generator:
    start_map = Map.GetMapID()
    timeout = time.time() + 150  # 2min30

    while Map.GetMapID() == start_map:
        if time.time() > timeout:
            break
        yield from Routines.Yield.wait(500)

    yield


def TakeReward(bot: Botting):
    bot.States.AddHeader("Take Reward")

    bot.Move.FollowAutoPath([TEKKS_POS])
    bot.Move.XYAndDialog(12503.0, 22721.0, 0x833907)

    # ⏳ attendre la fin du chrono + téléport Sparkfly
    bot.States.AddCustomState(_wait_end_dungeon, "Wait dungeon end (map change)")



def _begin_run_stats() -> Generator:
    STATS.run_count += 1
    STATS.current_run_start = time.time()
    yield


def _end_run_stats(success: bool) -> Generator:
    if STATS.current_run_start is None:
        yield
        return

    elapsed = int(round(time.time() - STATS.current_run_start))
    STATS.last_run_s = elapsed
    STATS.total_s += elapsed
    if STATS.fastest_s is None or elapsed < STATS.fastest_s:
        STATS.fastest_s = elapsed

    if success:
        STATS.success += 1
    else:
        STATS.fail += 1

    try:
        STATS.froggy_total = _scan_froggy_total()
    except Exception:
        pass

    STATS.current_run_start = None
    yield

# ---------------------------------------------------------------------------
# UI callbacks (Chahbek-style)
# ---------------------------------------------------------------------------

def _draw_texture():



    path = os.path.join(Py4GW.Console.get_projects_path(),"Bots","Froggy","farm_froggy.png")
    
    ImGui.DrawTextureExtended(
        texture_path=path,
        size=(100.0, 100.0),
        uv0=(0.0, 0.0),
        uv1=(1.0, 1.0),
        tint=(255, 255, 255, 255),
        border_color=(0, 0, 0, 0)
    )

def _draw_settings(bot: Botting):
    ImGui.separator()
    ImGui.text("Consumables")

    SET.hard_mode = ImGui.checkbox(
        "Hard Mode",
        SET.hard_mode
    )

    SET.use_summon_stage1 = ImGui.checkbox(
        "Use Summoning Stone - Stage 1",
        SET.use_summon_stage1
    )
    SET.use_summon_stage2 = ImGui.checkbox(
        "Use Summoning Stone - Stage 2",
        SET.use_summon_stage2
    )
    SET.use_conset_stage1 = ImGui.checkbox(
        "Use Conset - Stage 1",
        SET.use_conset_stage1
    )
    SET.use_conset_stage2 = ImGui.checkbox(
        "Use Conset - Stage 2",
        SET.use_conset_stage2
    )


    ImGui.separator()

    now = time.time()
    session_s = int(now - STATS.session_start)
    run_s = int(now - STATS.current_run_start) if STATS.current_run_start else 0

    # refresh counter (cheap)
    try:
        STATS.froggy_total = _scan_froggy_total()
    except Exception:
        pass

    ImGui.text(f"Timer (session): {session_s}s | (run): {run_s}s")
    ImGui.text(f"Runs: {STATS.run_count} | Success: {STATS.success} | Fail: {STATS.fail}")
    ImGui.text(f"Froggy (scepters): {STATS.froggy_total}")

    if STATS.last_run_s is not None:
        ImGui.text(f"Last: {STATS.last_run_s}s")
    if STATS.fastest_s is not None:
        ImGui.text(f"Fastest: {STATS.fastest_s}s")
    if STATS.success > 0:
        avg = int(round(STATS.total_s / STATS.success))
        ImGui.text(f"Average: {avg}s")

# ---------------------------------------------------------------------------
# Bot instance (global)
# ---------------------------------------------------------------------------
bot = Botting(
    bot_name=MODULE_NAME,
    upkeep_auto_combat_active=True,
    upkeep_auto_loot_active=True,
    upkeep_hero_ai_active=True,
)

# DO NOT call non-existing UI methods like enable_header/draw_texture.
bot.UI.override_draw_texture(_draw_texture)
bot.UI.override_draw_config(lambda: _draw_settings(bot))

try:
    bot.Properties.Disable("auto_inventory_management")
except Exception:
    pass
try:
    bot.Properties.Enable("hero_ai")
except Exception:
    pass
bot.Templates.Routines.UseCustomBehaviors()



def InitializeBot(bot: Botting) -> None:
    bot.States.AddHeader("Initialize Bot")
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))

def PopLegionnary():
    summoning_stone = ModelID.Legionnaire_Summoning_Crystal.value
    stone_id = Item.GetItemIdFromModelID(summoning_stone)
    imp_effect_id = 2886
    has_effect = Effects.HasEffect(Player.GetAgentID(), imp_effect_id)

    imp_model_id = 37810
    others = Party.GetOthers()
    cast_imp = True

    for other in others:
        if Agent.GetModelID(other) == imp_model_id:
            if not Agent.IsDead(other):
                cast_imp = False
            break

    if stone_id and not has_effect and cast_imp:
        Inventory.UseItem(stone_id)

def _upkeep_multibox_consumables(bot :"Botting"):
    while True:
        yield from bot.Wait._coro_for_time(15000)
        if not Routines.Checks.Map.MapValid():
            continue
        
        if Routines.Checks.Map.IsOutpost():
            continue
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Essence_Of_Celerity.value, 
                                            Skill.GetID("Essence_of_Celerity_item_effect"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Grail_Of_Might.value, 
                                                Skill.GetID("Grail_of_Might_item_effect"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Armor_Of_Salvation.value, 
                                                Skill.GetID("Armor_of_Salvation_item_effect"), 0, 0))
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Birthday_Cupcake.value, 
                                                Skill.GetID("Birthday_Cupcake_skill"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Golden_Egg.value, 
                                                Skill.GetID("Golden_Egg_skill"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Candy_Corn.value, 
                                                Skill.GetID("Candy_Corn_skill"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Candy_Apple.value, 
                                                Skill.GetID("Candy_Apple_skill"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Slice_Of_Pumpkin_Pie.value, 
                                                Skill.GetID("Pie_Induced_Ecstasy"), 0, 0))    
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Drake_Kabob.value, 
                                                Skill.GetID("Drake_Skin"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Bowl_Of_Skalefin_Soup.value, 
                                                Skill.GetID("Skale_Vigor"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.Pahnai_Salad.value, 
                                                Skill.GetID("Pahnai_Salad_item_effect"), 0, 0))  
        yield from bot.helpers.Multibox._use_consumable_message((ModelID.War_Supplies.value, 
                                                                Skill.GetID("Well_Supplied"), 0, 0))
        for i in range(1, 5): 
            Inventory.UseItem(ModelID.Honeycomb.value)
            yield from bot.Wait._coro_for_time(250)

def _apply_game_mode() -> Generator:
    if SET.hard_mode == True :
        Party.SetHardMode()
    else:
        Party.SetNormalMode()
    yield


def Setup(bot: Botting):
    bot.States.AddHeader("Setup")
    bot.Map.Travel(target_map_id=MAP_GADDS_ENCAMPMENT)
    bot.Wait.UntilOnOutpost()
    bot.States.AddCustomState(_apply_game_mode, "Apply Game Mode")

    
def _end_of_run_pause():
    yield from Routines.Yield.wait(500)
    yield



def Go_Out(bot: Botting):
    bot.Move.XY(-9451.37, -19766.40)
    bot.Wait.UntilOnExplorable()
    bot.Move.XYAndDialog (-8950, -19843, 0x84)
    bot.States.AddCustomState(lambda: PopLegionnary(), "Pop")
  
def ScheduleNextRun():
    print("🔁 Restarting full routine")
    yield from Routines.Yield.wait(1000)

    ActionQueueManager().ResetAllQueues()  # 🔥 CRITIQUE
    create_bot_routine(bot)

    yield



# ---------------------------------------------------------------------------
# Main Routine builder
# ---------------------------------------------------------------------------
def create_bot_routine(bot: Botting) -> None:
    InitializeBot(bot)
    Setup(bot)
    Go_Out(bot)
    Sparkly(bot)
    EnterDungeon(bot)

    # 🔁 POINT DE LOOP
    bot.States.AddHeader("[LOOP] Farm Start")

    bot.States.AddCustomState(_begin_run_stats, "Begin Run")
    FirstLevel(bot)
    SecondLevel(bot)
    TakeReward(bot)
    bot.States.AddCustomState(lambda: _end_run_stats(True), "End Run")
    bot.States.AddHeader("Loop: restart routine")
    bot.States.AddCustomState(ScheduleNextRun, "Schedule next run")
    TakeQuestandEnter(bot)






def EnterDungeon(bot: Botting):
    bot.States.AddHeader("Enter")
    path = [ (11676.01, 22685.0),(11562.77, 24059.0),(13097.0, 26393.0)   ,
 ]
    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path)
    bot.Wait.UntilOutOfCombat()

def FirstLevel(bot: Botting):
    bot.States.AddHeader("First Level")

    def follow_and_bless(path):
        bot.Templates.Multibox_Aggressive()
        bot.Move.FollowAutoPath(path)
        bot.Wait.UntilOutOfCombat()
        x, y = path[-1]
        bot.Dialogs.AtXY(x, y, DWARVEN_BLESSING_DIALOG, "Get Blessing")
    bot.States.AddCustomState(lambda: PopLegionnary(), "Pop")
    # =========================
    # Segment 0 — Départ → 1ʳᵉ bénédiction naine
    # =========================
    path_segment_0 = [
        (18092.0, 4315.0),
        (19045.95, 7877.0),  # bénédiction ici
    ]
    follow_and_bless(path_segment_0)
    if SET.use_conset_stage1 == True:
        bot.Multibox.UseAllConsumables()
if not getattr(bot, "_upkeep_running", False):
    setattr(bot, "_upkeep_running", True)
    bot.States.AddManagedCoroutine(
        "Upkeep Multibox Consumables",
        lambda: _upkeep_multibox_consumables(bot)
    )


    if SET.use_summon_stage1 == True:
        bot.States.AddCustomState(lambda: PopLegionnary(), "Pop")
    # =========================
    # Segment 1 — Après bénédiction #0 → bénédiction #1
    # =========================
    path2_part1 = [
        (16541.48, 8558.94),
        (13038.90, 7792.40),
        (11666.15, 6464.53),
        (10030.42, 7026.09),
        (10355.79, 8499.42),
        (6491.41, 5310.56),
        (5097.64, 2204.33),
        (1228.15, 54.49),  # point d'attente / aggro
    ]

    path2_part2 = [
        (141.23, -1965.14),
        (-1545.98, -5826.18),
        (-269.32, -8533.17),
        (-1230.10, -8608.68),
        (853.90, -9041.68),
        (1868, -10647),
        (1645, -11810),
        (1604.90, -12033.70),
        (1579.39, -14311.38),
        (7319.99, -17202.99),
        (7865, -19350),
    ]

    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path2_part1)
    bot.Wait.UntilOutOfCombat()

    bot.Move.FollowAutoPath(path2_part2)
    bot.Wait.UntilOutOfCombat()


def SecondLevel(bot: Botting):
    bot.States.AddHeader("Second Stage")

    def follow_and_bless(path):
        bot.Templates.Multibox_Aggressive()
        bot.Move.FollowAutoPath(path)
        bot.Wait.UntilOutOfCombat()
        x, y = path[-1]
        bot.Dialogs.AtXY(x, y, DWARVEN_BLESSING_DIALOG, "Get Blessing")

    # =========================
    # Segment 0 — Arrivée Level 2 → Bénédiction naine
    # =========================
    path_segment_0 = [
        (-11055.0, -5551.0),  # bénédiction ici
    ]
    follow_and_bless(path_segment_0)
    if SET.use_conset_stage2 == True:
        bot.Multibox.UseAllConsumables()
        bot.States.AddManagedCoroutine("Upkeep Multibox Consumables", lambda: _upkeep_multibox_consumables(bot))

    if SET.use_summon_stage1 == True:    
        bot.States.AddCustomState(lambda: PopLegionnary(), "Pop")

    # =========================
    # Segment 1 — Nettoyage première salle → bénédiction
    # =========================
    path_segment_1 = [
        (-11522.0, -3486.0),
        (-10639.0, -4076.0),
        (-11321.0, -5033.0),
        (-11268.0, -3922.0),
        (-11187.0, -2190.0),
        (-10706.0, -1272.0),
        (-10535.0, -191.0),
        (-10262.0, -1167.0),
        (-9390.0, -393.0),
        (-8427.0, 1043.0),
        (-7297.0, 2371.0),
        (-6460.0, 2964.0),
        (-5173.0, 3621.0),
        (-4225.0, 4452.0),
        (-3405.0, 5274.0),
        (-2778.0, 6814.0),
        (-3725.0, 7823.0),
        (-3627.0, 8933.0),
        (-3014.0, 10554.0),
        (-1604.0, 11789.0),
        (-955.0, 10984.0),  # bénédiction ici
    ]
    follow_and_bless(path_segment_1)

    # =========================
    # Segment 2 — Progression → Patriarch → bénédiction
    # =========================
    path_segment_2 = [
        (216.0, 11534.0),
        (1485.0, 12022.0),
        (2690.0, 12615.0),
        (3343.0, 13721.0),
        (4693.0, 13577.0),
        (5693.0, 12927.0),
        (5942.0, 11067.0),
        (6878.0, 9657.0),
        (6890.0, 7938.0),
        (7485.0, 6406.0),
        (9234.03, 6843.0),
        (8591.0, 4285.0),  # Patriarch
    ]
    follow_and_bless(path_segment_2)

    # =========================
    # Segment 3 — Fin du niveau → sortie
    # =========================
    path_segment_3 = [
        (8372.0, 3448.0),
        (8714.0, 2151.0),
        (9268.0, 1261.0),
        (10207.0, -201.0),
        (10999.0, -1356.0),
        (10593.0, -2846.0),
        (10280.0, -4144.0),
        (11016.0, -5384.0),
        (12943.0, -6511.0),
        (15127.0, -6231.0),
        (16461.0, -6041.0),
        (17565.0, -6227.0),
    ]
    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path_segment_3)
    bot.Wait.UntilOutOfCombat()

    # =========================
    # Ouverture de la porte
    # =========================
    # Move to door lever / signpost
    bot.Interact.WithGadgetAtXY(17922.0, -6241)

    # =========================
    # Segment 4 — Chemin vers le boss
    # =========================
    path_segment_4 = [
        (17623.87, -6546.0),
        (18024.0, -9191.0),
        (17110.0, -9842.0),
        (15867.0, -10866.0),
        (17555.0, -11963.0),
        (18761.0, -12747.0),
        (19619.0, -11498.0),  # bénédiction ici
    ]
    follow_and_bless(path_segment_4)

    # =========================
    # Segment 5 — Fin du chemin boss
    # =========================
    path_segment_5 = [
        (17582.52, -14231.0),
        (14794.47, -14929.0),
        (13609.12, -17286.0),
        (14079.80, -17776.0),
        (15116.40, -18733.0),  # zone boss final
    ]
    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path_segment_5)
    bot.Wait.UntilOutOfCombat()

    # =========================
    # Boss Fight
    # =========================

    # Coffre de fin
    # =========================
    # Approche du coffre Bogroot
    bot.Interact.WithGadgetAtXY(14982.66, -19122)
# fin de run, plus d'ennemis
    yield from Routines.Yield.wait(500)



def TakeQuestandEnter(bot: Botting):
    bot.States.AddHeader("Re-take quest")
    bot.States.AddCustomState(
    lambda: _take_or_retake_quest(bot),
    "Take/retake quest (Tekks)"
)
    bot.States.AddHeader("Next Run - Enter Dungeon")
    path = [ (11676.01, 22685.0),(11562.77, 24059.0),(13097.0, 26393.0)]
    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path)
    bot.Wait.UntilOutOfCombat()

def Sparkly(bot: Botting):
    bot.States.AddHeader("Go to Tekks")
    path = [
    (-8933.0, -18909.0),
    (-10361.0, -16332.0),
    (-11211.0, -13459.0),
    (-10755.0, -10552.0),
    (-9544.0, -7814.0),
    (-7662.0, -5532.0),
    (-6185.0, -4182.0),
    (-4742.0, -2793.0),
    (-2150.0, -1301.0),
    (71.0, 733.0),
    (1480.0, 3385.0),
    (2928.0, 4790.0),
    (4280.0, 6273.0),
    (5420.0, 7923.0),
    (6824.0, 9345.0),
    (7771.0, 11123.0),
    (8968.0, 12699.0),
    (10876.0, 13304.0),
    (12481.0, 14496.0),
    (13080.0, 16405.0),
    (13487.0, 18372.0),
    (13476.0, 20370.0),
    (12503.0, 22721.0),
    ]
    
    bot.Templates.Multibox_Aggressive()
    bot.Move.FollowAutoPath(path)
    bot.Wait.UntilOutOfCombat()

    x, y = path[-1]
    bot.Dialogs.AtXY(x, y, DWARVEN_BLESSING_DIALOG, "Get Blessing")
    # --- Tekks ---
    bot.States.AddCustomState(
    lambda: _take_or_retake_quest(bot),
    "Take/retake quest (Tekks)"
)


bot.SetMainRoutine(create_bot_routine)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    bot.Update()
    bot.UI.draw_window()

if __name__ == "__main__":
    main()
