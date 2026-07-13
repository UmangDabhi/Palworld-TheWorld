Palworld local co-op Git relay for this world
=============================================

World:
C:\Users\ud301\AppData\Local\Pal\Saved\SaveGames\76561199293044696\BB11D4F74118BDEE509BFF9C1562BB41

Important:
The normal BAT files use relative paths. After they are inside a Palworld world
folder, they work from that folder on any PC. Hazeki's Windows username, Steam
ID folder, and world path can be different.

GitHub:
https://github.com/UmangDabhi/Palworld-TheWorld.git

Characters found:
- 00000000000000000000000000000001 = Shine
- 7A63391C000000000000000000000000 = Hazeki

Files you use:
- 1-PULL-AND-SWAP.bat
- 2-PUSH-WORLD.bat

Install file:
- INSTALL-TO-LIVE-WORLD.bat

First setup on Umang/Shine PC:
1. Close Palworld completely.
2. Run INSTALL-TO-LIVE-WORLD.bat from this setup folder.
3. When asked which character this PC should host as, choose 1 = Shine.
4. It will copy the BAT files into the live world folder, initialize Git there,
   use the Palworld-TheWorld repo, commit the whole world folder, and push.

First setup on Hazeki PC:
1. Clone/pull the repo into Hazeki's matching Palworld world folder. His path
   does not need to match Umang's path.
2. Close Palworld completely.
3. Run 1-PULL-AND-SWAP.bat.
4. When asked which character this PC should host as, choose 2 = Hazeki.

If Hazeki uses the ZIP instead of Git clone:
1. Extract the ZIP anywhere.
2. Run INSTALL-TO-LIVE-WORLD.bat.
3. Paste Hazeki's own Palworld world folder path when asked.
4. Choose 2 = Hazeki when asked for the local character.

About Shine's missing client GUID:
Do not guess it. If the BAT says ONE-TIME BOOTSTRAP NEEDED, follow its steps.
That means Palworld needs to create Shine's normal client GUID once. After that,
the script records it and future pull/swap/push cycles are normal.

Normal daily use:
- Before playing/grinding: close game, run 1-PULL-AND-SWAP.bat, then play.
- After playing/grinding: close game, run 2-PUSH-WORLD.bat.

Safety:
- Never run the BAT files while Palworld is open.
- The scripts create backups in .palworld-relay\backups before swap/push.
- The scripts write logs in .palworld-relay\logs.
- Pull refuses to run if you have unpushed local changes.
- Git origin is locked to https://github.com/UmangDabhi/Palworld-TheWorld.git
- The swap is not just renaming files; it rewrites player save internals,
  Level.sav player references, and guild references.
