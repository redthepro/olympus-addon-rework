# CurseForge installation readiness

This fork is not published on CurseForge. A GitHub fork or a local test ZIP does
not create a CurseForge project. Do not distribute the expiring `-testN` builds
through CurseForge; `scripts/package.sh` documents that restriction.

## Current member experience

The ordinary addon package contains the `Olympus`, `Olympus_Arena` and
`OlympusSiteAggregate` folders at its root, with their matching TOCs. The local
Python collector, setup scripts and hosted-service credentials are not in it.
The current shared service removes the need for every member to register API
credentials, but transport into WoW is still manual copy/paste. The private EC2
service is running; public `/census` routing still awaits AWS permissions, and
Forever fetching is disabled pending official endpoint documentation.

CurseForge manages addon files. WoW addon Lua cannot issue ordinary HTTPS
requests, launch/install a desktop companion or read arbitrary files outside its
sandbox. Packaging a collector script in the addon ZIP would not run it. No
automatic website synchronization is implemented in this build.

## Automatic-update design

For automatic census delivery, members would need a separate one-time companion
installation. It should download only the server's aggregate snapshot over HTTPS,
require no API secrets or Python setup, detect the selected WoW installation and
flavor, and provide an explicit choice if multiple installations exist. Its data
must live in a dedicated companion-owned addon folder, separate from the folders
CurseForge replaces on updates. Updates must be atomic, bounded and parsed as
data; arbitrary downloaded Lua must never be executed. A last-good snapshot can
be retained only within its TTL. Unsupported/empty server responses must not
replace counts with zero. The companion must not inspect game memory, inject
code or automate input.

WoW loads addon files at login/UI load, so downloading a new file does not update
the already-running Lua state. A player reload or next login is still needed;
first-time installation of a new addon folder can require a full game restart.
The companion must explain this instead of silently simulating a reload command.
This is a design requirement, not a feature shipped in the current Python setup.

## Before a CurseForge release

1. Create a separate fork project and obtain its project ID. The source still
   carries upstream project ID `1708200` and upstream update/download links;
   these must be replaced with the fork's real identity before publication.
   Do not upload to or impersonate the original author's project.
2. Choose a non-expiring fork version, verify the correct Forever game-flavor
   metadata and publish the tested addon-only ZIP. Use the existing packaging
   script, which also excludes wager-only modules. Raw repository auto-packaging
   has not been configured or verified for this fork.
3. Document migration from the original Olympus package. Both currently use the
   same addon folder names; installing/updating both can replace the other's
   files. Keep only the chosen project managed by CurseForge. Preserve the user's
   SavedVariables during migration.
4. Test a fresh CurseForge install, an app-managed update, the selected game
   flavor, SavedVariables retention and a companion data refresh. Packaging
   checks alone are not evidence that these app/game flows work.
5. Complete public census routing and official Forever API validation before
   advertising automatic API census data. Show the live census when unavailable.

References: [CurseForge installation FAQ](https://support.curseforge.com/support/solutions/articles/9000198422-addons-faq),
[automatic packaging](https://support.curseforge.com/support/solutions/articles/9000197281),
[PackageMeta](https://support.curseforge.com/support/solutions/articles/9000197952-preparing-the-packagemeta-file).
