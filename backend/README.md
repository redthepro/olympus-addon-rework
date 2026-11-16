# Olympus local API census companion

The companion runs on your PC, reads Blizzard's public guild roster API, and caches
the last successful snapshots in a local SQLite file. No hosted server, RDS,
shared database, or router forwarding is required. WoW cannot make web requests;
copy the companion's export into the addon's paste window while the game is running.

## Windows setup

Requires Windows PowerShell 5.1+ and Python 3.10+ on PATH. No third-party Python packages.

1. Create your own Battle.net application at https://develop.battle.net/access/clients .
   Public roster collection uses its client ID and secret, not your Battle.net password.
2. Run `companion/Setup-Companion.cmd`. Enter the client ID, hidden secret, region,
   game version, and guild slug (plus realm for Retail). Defaults are **US, Forever,
   Normal ruleset, olympus**. The secret is encrypted
   for your Windows account with DPAPI; it is never passed to the addon.
3. Run `companion/Start-Companion.cmd`. Keep its console open. The browser opens
   http://127.0.0.1:8787 after the local listener is ready. Ctrl+C stops collection.
   A second launch reports that the port is occupied without opening another tab.
4. Check that a real roster has been fetched and review its scope/count/dates.
   A failed request is not an empty guild.
5. Press **Prepare snapshot**, then **Copy snapshot**. If your browser blocks clipboard
   access, use Ctrl+C on the selected text. In WoW open **Census →
   API census → Paste snapshot**, or type `/oly censusapi paste`. Review and import.
   `/oly cloud paste` remains an alias. No reload is required for import.

The experimental Forever adapter uses `profile-forever-{region}`, the realm-less
`/data/wow/guild/{guildSlug}/roster` route. With a ruleset scope (`normal`, `pvp`, `roleplay`, or `roleplay-pvp`), it also fetches the
guild summary and requires its ruleset name to match the selected scope and its guild ID to match
the roster. No numeric ruleset ID is assumed. Layers do not create separate counts.
The alternative `global` scope does not enforce a ruleset. These routes follow
firsthand community observations, not confirmed Blizzard documentation. The usual
realm-qualified endpoint remains separate for other namespaces.

Forever API support must be verified against your actual target. No real Forever roster has been
validated without credentials and a selected guild; synthetic tests do not prove
live coverage. Unsupported guilds cannot be counted authoritatively by this tool.

Settings and cache live in `%LOCALAPPDATA%\OlympusCensus`, outside the source folder.
Run setup again to change credentials; accept the default to keep existing targets
and polling settings. For unattended rotation, run `Setup-Companion.ps1 -CredentialsOnly`
with your client ID and secure-string secret. Add guilds to `targets` in `settings.json`:
each entry is `[region, namespace, realmOrScope, guildSlug]`. The starting Forever
target is `["us","profile-forever-us","normal","olympus"]`. Leave `protectedSecret`
unchanged. Restart after editing settings. Use API slugs, not guessed display names.

Use your own developer credentials; do not distribute a shared secret inside a
companion installation. This local workflow is for people configuring their own
application. The API binds only to loopback and checks Host headers. Other processes
running as your Windows account can access your local data.

## Data semantics

Each complete valid response atomically replaces one guild roster. Realm/character
IDs deduplicate members. Conflicting records, wrong scopes, older source dates, and
malformed data preserve the last good snapshot. Retry-After and exponential backoff
apply after failures. A guild's retry delay does not block healthy guilds; a shared
429 cooldown survives restarts. Polling defaults to 15 minutes, with a five-minute minimum.

Fetch time and source Last-Modified are separate; missing source time stays unknown.
When an unchanged response omits a previously supplied source date, that date is
retained; changed responses without chronology cannot replace dated data.
Online counts are unavailable, never inferred. Counts are characters, not people.
The API report keeps per-guild dates. The main Census can combine eligible snapshots and live fallback counts, explicitly labelled as independently dated membership observations. Only aggregate
scope/count/date rows are exported; member records remain in the local cache.
Guild rows fetched more than 24 hours ago are withheld from exports and imports.
The addon keeps showing fresh rows from a mixed-age saved snapshot. Older or
conflicting imports preserve the saved baseline. During a connection failure, the
dashboard retains its last successful view and labels it as cached; prepared text
is cleared when data changes, expires, or the connection fails.

The original live census remains available. The display overlay does not replace guild
verification, permissions, live online counts, or chat networking. In-game acceptance
checks are in [CLOUD-CENSUS.md](../docs/CLOUD-CENSUS.md).

## Development

```
python -m unittest discover -s backend -v
bash scripts/check.sh
```

For a manual local run, set `BNET_CLIENT_ID`, `BNET_CLIENT_SECRET`, `CENSUS_TARGETS`
(JSON array), and `CENSUS_DB` (SQLite path), then run `python backend/service.py`.
Never place secrets in shell history or git. Optional `CENSUS_INTERVAL` and
`CENSUS_PORT` configure polling and the local listener. `/healthz` reports worker
state, `/v1/census` returns snapshots/attempts, and `/v1/census/export` produces
bounded addon import text.

References:
- https://community.developer.battle.net/documentation/guides/using-oauth/client-credentials-flow
- https://community.developer.battle.net/documentation/world-of-warcraft/profile-apis
- https://community.developer.battle.net/documentation/world-of-warcraft/guides/namespaces
- Firsthand Forever observations (not an official compatibility guarantee):
  https://github.com/Pinta365/blizzard_api/blob/6d3c0a12e076ff1f2c10d93ba4f530995411171a/src/wow_forever/guild.ts
