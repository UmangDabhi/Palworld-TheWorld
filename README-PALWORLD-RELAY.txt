Palworld local co-op Git relay
==============================

Repository:
https://github.com/UmangDabhi/Palworld-TheWorld.git

This relay is only for the matching local multiplayer world. It is not a
dedicated-server migration tool.

Player identities
-----------------

Host slot (always):
Players\00000000000000000000000000000001.sav

Shine / Umang client GUID:
B876A457000000000000000000000000

Hazeki / Harsh client GUID:
7A63391C000000000000000000000000

When Shine hosts, the player files must be:
- 00000000000000000000000000000001.sav = Shine
- 7A63391C000000000000000000000000.sav = Hazeki

When Hazeki hosts, the player files must be:
- 00000000000000000000000000000001.sav = Hazeki
- B876A457000000000000000000000000.sav = Shine

Agreed session policy:
- When Shine hosts, Hazeki may join; Hazeki's joined-session progress updates
  7A63391C000000000000000000000000.sav.
- When Hazeki hosts, Shine does not join; Shine remains preserved in
  B876A457000000000000000000000000.sav.
- A leftover client-GUID save for the current host is accepted only when it is
  proven to be the same character instance as the host save. If it has a DPS
  sidecar, every active Pal instance must already exist in the active host DPS
  sidecar. The relay moves the complete stale alias set into the new safety
  backup instead of deleting it; any unique alias Pal stops the operation.

Normal use
----------

1. Decide who will host. Make sure Palworld is fully closed on that PC.
2. The host runs 1-PULL-AND-SWAP.bat.
3. Only open Palworld after the BAT prints READY and says validation passed.
4. When Shine hosts, Hazeki may join. When Hazeki hosts, Shine stays out.
5. The host closes Palworld completely and runs 2-PUSH-WORLD.bat.
6. The client does not push their own local world after joining someone else.

Diagnostics
-----------

With Palworld closed, run 3-DIAGNOSE-WORLD.bat whenever a player appears fresh,
a player model is missing, pull/push stops, or the Players folder has an
unexpected file. It is read-only except for its timestamped support log.

The report includes the active and expected Steam account, configured/current
host, Git commit and status, stashes, save hashes, every ordinary player and _dps
sidecar, internal UIDs and character instances, guild members, guild map-marker
owners, duplicate/dormant host aliases, character levels, item/Pal counts when
ownership links validate, the expected file layout, recent backups, and recent
relay logs. Share the newest
.palworld-relay\logs\*-diagnose-world.log instead of assembling one-off
commands. A warning diagnoses the current state; it never normalizes, swaps,
restores, stages, commits, or deletes a save.

After a successful host swap, git status is intentionally dirty. Level.sav,
state.json, and the host player save change; the incoming host's old client
file is removed, and the outgoing host's client file is added. Matching
_dps.sav sidecars rotate the same way. Do not restore these paths. Play, close
Palworld, then use 2-PUSH-WORLD.bat to commit the prepared host layout.

The BAT files and every script/tool they need are committed to this repository.
Each successful pull updates them for the next run.

Pull conflict question
----------------------

If pull finds local changes, it shows every changed path and asks y/n. First ask
the other player whether they hosted/played and pushed the progress that should
win. Enter y only when the GitHub world is the version to continue from.

Choosing y does not force-reset anything. The relay creates a full save backup
and a named Git stash, validates GitHub's world in an isolated folder, performs
the swap there, validates it again, and only then installs it. Choosing n stops
without installing the GitHub world.

Safety and validation
---------------------

- Never run either BAT while Palworld is open.
- LocalData.sav is machine-local. The relay marks it skip-worktree and never
  stages it in a save commit. Pull carries the local copy through staging and
  verifies its SHA-256 hash before and after installation; a mismatch stops the
  relay and restores the pre-pull map/fog file.
- Pull uses fetch plus fast-forward only. It never force-pulls or merges binary
  worlds. Diverged or unpushed commits stop with an explanation.
- Push refuses when GitHub has newer commits.
- Pull, swap, and push validate player files, internal player IDs, character
  instances, all six item containers (inventory, drop slot, key items, food,
  weapon loadout, and armor/equipment), every referenced dynamic item record,
  party/Palbox containers, Pal ownership, guild handles, and the exact
  two-player file layout.
- Swap compares exact inventory/equipment fingerprints and exact Pal instance
  sets before and after rotation. Matching counts alone are not accepted.
- Guild parsing supports both the older layout and the July 2026 layout with
  map markers, marker owners, chest roles, player roles, and role permissions.
  A dormant outgoing-host alias is neutralized only when its guild has no
  handles, bases, base points, markers, or other members; ambiguous guild data
  stops the swap instead.
- Files named Players\<player-guid>_dps.sav are dimensional Pal storage
  sidecars, not extra players. They are backed up, committed, validated, and
  rotated with their owner; exact sidecar fingerprints must survive the swap.
- Pals acquired or transferred during local co-op can retain the session host
  UID as provenance. Current ownership is validated from OwnerPlayerUId and the
  player's party/Palbox placement, while slot/key provenance must remain
  internally consistent.
- Completed Pal expeditions can leave stale per-Pal assignment flags after a
  host swap. Pull and push clear flags whose Pal instance is absent from the
  expedition station's active team. They stop if an expedition is still active:
  finish and claim it on the current host before transferring the world.
  The diagnostic report shows active and orphaned expedition-lock counts.
- Palworld 1.0 World Tree recovery-party records use 64-bit map values. The
  bundled parser preserves those records and rewrites their player GUID keys
  during host swaps; unknown new map-object payloads remain byte-exact.
- Backups are in .palworld-relay\backups. Each new backup has a manifest with
  SHA-256 hashes and the Git commit ID.
- Logs are in .palworld-relay\logs.
- No player save is manually renamed or silently deleted by the BAT files.
- Git origin is locked to the repository URL shown above.

If an old pull script blocks the one-time relay upgrade
-------------------------------------------------------

Close Palworld, open PowerShell in this world folder, and run:

git update-index --skip-worktree -- LocalData.sav
git stash push --include-untracked -m "relay-upgrade-before-pull"
git pull --ff-only origin main

Then run the newly downloaded 1-PULL-AND-SWAP.bat. The named stash remains local
until both players confirm it is no longer needed.

Push permission failure
-----------------------

If GitHub says permission was denied, the commit remains safe on that PC. Umang
must add Harsh's GitHub account as a collaborator with write access. After access
is fixed, run:

git push -u origin main

Do not create another save commit just because the network push failed.
