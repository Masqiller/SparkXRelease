"""SparkX scoring rules (public, see docs/RULES.md). All values are points."""

RULES_VERSION = '1.2'       # 1.2: report window at a survivor, idle limit

SCORING = dict(
    rescue=100,                 # confirmed rescue
    rescue_via_hint=50,         # extra: target of a released hint (clue-led rescue)
    accuracy_bonus_max=20,      # linear from 20 (0 m error) to 0 (report tolerance)
    return_to_base=100,         # EndMission inside base zone
    return_requires_rescues=1,  # minimum rescues for the return bonus
    time_bonus_per_min=5,       # per full remaining minute at EndMission (only if all rescued + at base)
    false_report=-25,           # rejected report (NOT_STOPPED/NO_SURVIVOR/NO_LOS/POSITION_MISMATCH/ALREADY)
    late_report=-20,            # stayed at a survivor longer than REPORT_WINDOW_S without a confirmed rescue (once per survivor)
    idle=-10,                   # every full IDLE_LIMIT_S without moving IDLE_MOVE_M
    collision=-10,              # new collision event with a solid object (1 s debounce)
    survivor_contact=-50,       # robot touches a survivor body
    fire_damage_per_s=-2,       # per second inside a fire hazard radius
    flip=-100,                  # robot rolled/pitched over (robot disabled)
    intervention=-75,           # manual intervention recorded by organizers
    map_quality_max=100,        # map IoU-based score, awarded at the end
    semantic_max=50,            # correctly mapped landmarks/survivors in the submitted record
)

HEALTH = dict(fire_hazard_per_s=4.0, fire_danger_per_s=15.0)
COLLISION_DEBOUNCE_S = 1.0
SURVIVOR_CONTACT_DEBOUNCE_S = 10.0
FLIP_ANGLE_RAD = 1.0
REPORT_RATE_LIMIT_S = 3.0
# Timing rules (rules v1.2). Mission time, i.e. simulation seconds while the mission is RUNNING. 0 switches a rule off.
REPORT_WINDOW_S = 10.0          # from "within the rescue radius with a clear line of sight" to the confirmed rescue
IDLE_LIMIT_S = 60.0             # longest the rover may go without moving IDLE_MOVE_M
IDLE_MOVE_M = 0.5
TIME_WARNINGS_S = (300.0, 120.0, 60.0)
