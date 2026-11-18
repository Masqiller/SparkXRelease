# SparkX evaluator guide (`hosting/`)

This folder is the **checker**. For each team it:

1. checks that the submission zip was handed in properly;
2. builds the team's code into a clean copy of this stack;
3. runs it on a world in competition mode;
4. stores the referee's score and keeps the leaderboard.

It is the same tool the organisers use for the official runs.

Who uses it:

- **Evaluators**: run every team's zip and keep the leaderboard.
- **Teams**: may use `check` (section 4) to make sure their own zip will be accepted before they upload it.

Everything below runs from the root of your clone of this branch. In the examples that folder is
called `$SPARKX`. Set it once per terminal, or add the line to `~/.bashrc`:

```bash
export SPARKX=~/SparkXEval        # wherever you cloned the evaluator branch
cd $SPARKX
```

## 1. What the machine needs

- **Everything the team quick start needs:** Ubuntu 24.04, ROS 2 Jazzy and the `apt install` line in the main [`README.md`](../README.md) §4, and a GPU that runs Gazebo.
- **bubblewrap**, the sandbox for team software: `sudo apt install bubblewrap`. Without it, runs still work, but a team's code can read every file of your user.
- **Memory:**
  - The simulator and referee need about 2.5–3 GB for a medium world.
  - A team's software is limited to 8 GB by default.
  - 16 GB of RAM is comfortable for the small world. Use more for large worlds.
- **One evaluation at a time.** The tool refuses to start a run while another simulation is running.

## 2. Get the evaluator branch

Use a folder of its own, separate from any team work:

```bash
git clone -b evaluator https://github.com/Masqiller/SparkXRelease.git ~/SparkXEval
export SPARKX=~/SparkXEval && cd $SPARKX
git branch                         # * evaluator
```

Already have a clone of `main`? `git fetch origin && git switch evaluator` gives you the same files.
`git switch main` goes back.

## 3. One-time setup

```bash
cp hosting/host_config.example.json hosting/host_config.json      # then edit it for this machine (table below)
python3 hosting/sparkx_host.py setup
```

`setup` builds `sparkx_ws/` from scratch, which takes under a minute on a fast machine. It then checks the stack against its seal. The end of
its output must be:

```
Summary: 8 packages finished [15.1s]
stack integrity: OK  (741 locked files, fingerprint ea4c2e955e1c25ca)
sandbox for team software: available (bubblewrap)
```

The fingerprint must be the one the organisers announced for the release.

If setup prints `REFUSED: this copy of the stack differs from its seal`:

1. something in `sparkx_ws/` was changed;
2. `git status` shows what changed;
3. `git checkout -- <file>` restores it;
4. run `setup` again.

Never evaluate on a stack that does not verify.

Check the referee's timing rules once (no simulator needed):

```bash
python3 hosting/test_timing_rules.py       # last line: ALL PASSED
```

`hosting/host_config.json` holds the settings of your machine. It is never committed. Delete it to
use the defaults.

| Key | Default | Meaning |
|---|---|---|
| `team_memory` | `8G` | memory limit of a team's software (the rules promise teams 8 GB) |
| `team_cpus` | `8` | CPU cores for a team's software (applied only if your system allows it) |
| `org_memory` | none | memory limit of simulator + referee (e.g. `3200M`); leave empty unless the machine is short of memory |
| `textures` | `full` | `lite` halves texture memory. Use `full` for scored runs |
| `sandbox` | `auto` | `on` refuses to run without bubblewrap, `off` never sandboxes |
| `min_free_mb` | 0 | refuse to start a run with less free memory than this |
| `extra_setup`, `team_visible_paths` | none | only if Nav2 / SLAM come from your own workspace instead of `apt`: its `setup.bash`, and that folder |
| `dashboard_port`, `ui_port` | 8080, 8090 | the referee's dashboard and this tool's control panel |

## 4. Check a submission (did they hand in properly?)

Put the teams' zips into `hosting/submissions/`. The file name is the team name:
`roborescuers.zip` or `roborescuers_submission.zip` → team `roborescuers`.

```bash
python3 hosting/sparkx_host.py check hosting/submissions/roborescuers_submission.zip
```

`check` runs nothing. It accepts a zip only if it holds these files and nothing else:

- `perception.py`, `memory.py`, `mission.py` and `team.yaml`;
- optional data files in a `models/` folder.

The folder layout inside the zip does not matter. Real outputs:

```
demo_team_submission.zip (team "demo_team"): ACCEPTED

missing_team.zip (team "missing_team"): REJECTED
  team.yaml: missing

extra_file.zip (team "extra_file"): REJECTED
  notes.py: not one of the four team files and not inside models/

syntax_error.zip (team "syntax_error"): REJECTED
  src/sparkx_team/sparkx_team/mission.py: does not compile: line 295: invalid syntax
```

The exit code is 0 for accepted and 1 for rejected. Other reasons for a rejection:

- code inside `models/`;
- sub-folders in `models/`;
- a file over 500 MB, or more than 1.5 GB in total;
- a file name that appears twice.

**Teams:** make your zip with `python3 sparkx_ws/tools/sparkx_lock.py pack <team_name>` (on `main`),
then run `check` on it from this branch.

## 5. Run a submission (does the code work?)

```bash
python3 hosting/sparkx_host.py run hosting/submissions/demo_team_submission.zip --world practice_small_day
```

What one run does:

1. checks the zip;
2. copies the sealed stack into `hosting/work/<team>/`, puts the four files in and rebuilds only `sparkx_team`;
3. starts the simulator and the referee in competition mode;
4. starts the team's software in the sandbox, with its memory limit;
5. starts the mission when navigation reports ready.

The run ends when the team ends the mission, at the time limit, or when the team's software dies.
Real output of the starter files:

```
[12:44:18] demo_team: copying the sealed stack
[12:44:19] demo_team: files accepted, building sparkx_team
[12:44:52] simulator and referee are up after 32 s (world practice_small_day)
[12:44:52] team software started (sandboxed, memory limit 1000M)
[12:45:06] navigation active after 14 s
[12:45:11] MISSION STARTED
[12:45:41]       25s MISSION_FINISHED: END_MISSION:COMPLETED (in base) (+0)
[12:45:45] RESULT demo_team on practice_small_day: score 27, rescued 0/4, END_MISSION:COMPLETED (in base), mission time 25 s
```

That run had `team_memory` set to 1000M; with the defaults the line says 8G. The starter only drives 3 m and back,
so 27 points is what the unchanged starter scores. A real solution runs for most of the time limit
(15 min on a small world, 20 medium, 25 large).

Options:

- `--gui` shows the Gazebo window. Use it only to watch a run, never on a machine short of memory.
- `--time-limit <s>` overrides the world's time limit, for quick tests only, never for a scored run.

While a run is on:

- the referee's dashboard is at `http://127.0.0.1:8080` (the panel in section 6 shows the link with its token);
- `python3 hosting/sparkx_host.py stop` stops it.

Where the results are:

| Path | What |
|---|---|
| `hosting/results/runs/<date>_<team>_<world>/summary.json` | the score: `official_score`, `final_score`, `score_breakdown`, `rescued`, `finish_reason`, `disqualified`, `rule_violations`, `stack_integrity` |
| `…/events.jsonl`, `…/team_map.png`, `…/evidence/` | every scored event, the team's map, the snapshots sent with rescue reports |
| `…/host.json` | which zip ran (its SHA-256), whether it was sandboxed, the memory limit |
| `hosting/results/team_logs/<date>_<team>_<world>/` | `team.log` (the team's own output: look here when their code crashed), `organiser.log`, `roslog/` |
| `hosting/results/leaderboard.csv` | rebuilt after every run |

## 6. The control panel (the button)

```bash
python3 hosting/sparkx_host.py ui           # open http://127.0.0.1:8090 ; Ctrl-C quits (and stops a run)
```

The panel shows:

- every zip in `hosting/submissions/`, with the result of `check` and every run it has had;
- a world selector;
- a **Build & run** button per team;
- **Run every accepted team on this world**, which queues them one after another.

While a run is on, it shows the mission clock, the score, the referee's events and a link to the live dashboard. The
leaderboard is at the bottom.

## 7. Worlds

- `practice_small_day` ships with the stack and is always offered: use it to test the pipeline.
- Competition worlds come from the same generator with a **secret seed**:

```bash
python3 hosting/sparkx_host.py new-world --name final_A --seed <secret number> --size medium --difficulty normal --lighting day
```

They land in `hosting/worlds/<name>/`. Look at `hosting/worlds/<name>/private/preview.png` (survivors,
clues, fires) before you use one.

- `private/` is the answer sheet: never commit, upload or share it, and never write the seed anywhere public.
- `.gitignore` keeps the whole folder out of git.
- Every evaluator must run a team on the *same* world folder. Copy it between machines privately, or have one machine run everything.

## 8. Leaderboard

```bash
python3 hosting/sparkx_host.py board
```

Real output:

```
 # team                    total  rescued  per world
 1 demo_team                  27        0  practice_small_day: 27 (0/4)
```

The total is the sum, over all worlds, of each team's best official score on that world. A disqualified run counts 0.

## 9. Evaluator checklist (one per submission)

1. The zip arrived before the deadline and its name is the team name.
2. `check` says ACCEPTED. If it is REJECTED, tell the team the exact reason that `check` printed.
3. `setup` (or `python3 sparkx_ws/tools/sparkx_lock.py verify`) still says `stack integrity: OK` with the announced fingerprint.
4. `run` on each competition world, one run at a time, with full textures and no `--time-limit`.
5. In `summary.json`:
   - `disqualified` is false;
   - `rule_violations` is empty;
   - `stack_integrity.status` is `ok`;
   - `finish_reason` makes sense.
6. If the run ended early, read `team.log`. A crash in the team's code counts as their result, but a
   machine problem does not: then run it again.
7. Note the `official_score`. The leaderboard keeps the best run per world.
8. Before results are announced, read the code of the top teams. A team is disqualified on a world if its code:
   - touches `/sparkx/referee_private/*`;
   - talks to Gazebo directly;
   - reads ground truth from files;
   - was driven by a person.

   See `sparkx_ws/docs/RULES.md`.

## 10. Troubleshooting

| Message | What to do |
|---|---|
| `the organiser stack is not built yet` | run `setup` |
| `does not match its seal` | `git status`, restore the files, run `setup` again |
| `a simulation is already running` | wait, or `python3 hosting/sparkx_host.py stop`; close any other Gazebo you started |
| `simulator / referee did not come up` | read `hosting/results/team_logs/<run>/organiser.log`; usually memory or the GPU |
| `the team software exited during start-up` | read `team.log` in the same folder: an error in the team's code |
| `navigation did not become active` | relaunch once; if it repeats with the team's `team.yaml`, their Nav2 settings are broken |
| `bubblewrap ... does not work` | `sudo apt install bubblewrap`, or set `"sandbox": "off"` only for a test run |

## Updating

When the organisers publish a new release:

1. `git pull` (on this branch);
2. `python3 hosting/sparkx_host.py setup` again;
3. compare the printed fingerprint with the announced one.

Results of runs made on the old release stay in `hosting/results/` and remain on the leaderboard.
