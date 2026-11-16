# Blizzard policy review

Reviewed 2026-11-15. This is an engineering review of this fork, not Blizzard
approval, an anti-cheat certification, or a guarantee against account action.
Blizzard retains authority to interpret its rules and disable addon functions.

## Sources and boundaries

- [Blizzard EULA](https://www.blizzard.com/en-us/legal/08b946df-660a-40e4-a072-1fbde65173b1/blizzard-end-user-license-agreement): unauthorized automation, client interference and data collection restrictions.
- [UI Add-On Development Policy](https://us.forums.blizzard.com/en/wow/t/ui-add-on-development-policy/24534): free distribution, visible source and player-impact restrictions.
- [Developer API terms](https://www.blizzard.com/en-us/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use): API access and data obligations, including the 30-day maximum retention in section 18.
- [Official namespaces](https://community.developer.battle.net/documentation/world-of-warcraft/guides/namespaces) and [profile API reference](https://community.developer.battle.net/documentation/world-of-warcraft/profile-apis).

The API terms page returned HTTP 403 to direct retrieval during this review;
its Blizzard-hosted indexed text confirmed the retention requirement. This is
not a claim that every contractual clause or every upstream feature was certified.

## Enforced controls

The companion uses OAuth application credentials and HTTPS to Blizzard's API.
It does not attach to WoW, inspect process memory, inject code, intercept game
traffic or send keyboard input. The addon imports bounded text; it does not
execute that text or connect to the companion. There is no automated gameplay
bridge. Credentials remain outside the addon and exported data.

Only the documented retail profile namespaces can reach the network. The
Forever adapter's synthetic tests are retained, but production requests are
blocked before OAuth or network access. No environment flag bypasses this.
Community observations and successful HTTP responses alone do not establish
official support. US PvE continues to use the existing live addon census.

The collector spaces requests at most ten per second and respects HTTP 429
backoff. Removed targets are deleted at startup. HTTP 403, 404 and 410 remove
the previous roster rather than presenting potentially withdrawn data. Transient
failures retain last-good data only within the retention limit.

SQLite roster data, associated metadata and old attempt records expire at 29
days after their last fetch/attempt, providing margin below 30 days. Cleanup
runs at startup, before database operations, and during collector waits, even
with no credentials or during long API backoff. SQLite secure_delete is enabled.
The addon deletes a saved import when any row reaches 29 days, on login and
each minute, and checks before displaying/importing. Its 24-hour census-display
freshness rule remains separate from retention. Mixed-age imports cannot sneak
an over-retention row into SavedVariables.

Recruiting handoffs remain disabled, including their old protocol and saved
opt-ins. Existing Forever recruiting produces invite drafts for the player to
copy/paste and submit. It does not silently auto-join players or bypass protected
actions. Respect declines and avoid repeated unsolicited messages. Rate limits
alone do not make disruptive recruiting acceptable. Treasury references to
donations concern in-game guild assets, not payment for addon development.

Source remains readable and free. No premium unlock, paid API access tier,
developer donation solicitation or external advertising was added. Community
branding is not an assertion of Blizzard endorsement.

## Local data and operator responsibilities

The companion stores roster identifiers/names and guild metadata locally in
`%LOCALAPPDATA%\OlympusCensus\census.sqlite3`; API exports contain aggregate guild
counts and dates. It sends requests to Blizzard and serves the local dashboard
only on loopback. There is no analytics upload. The optional hosted collector serves only aggregate snapshots at `/census`; it does not expose member rows or private diagnostics.
The addon retains the user's manual import in its SavedVariables. Existing
in-game census communication remains separate from the API snapshot.

Code cannot delete files while the PC/game/companion is off. Startup cleanup
precedes reuse, but users must not keep inactive installations, database copies,
clipboard-history exports or SavedVariables backups past the 30-day limit.
Delete local API data when discontinuing use. Close the companion before deleting
its `census.sqlite3`, `census.sqlite3-wal` and `census.sqlite3-shm` files if present.
Use `/oly censusapi clear` for the addon copy, then log out normally. Also remove
old exports and backups; the addon cannot manage arbitrary external copies.
Do not delete unrelated WoW settings. Removing credentials in the developer
portal and deleting local `settings.json` ends use of that application's secret.

Register your own application and accept Blizzard's current API terms. Do not
ship a shared client secret, even encrypted: a distributed program must have
the means to decrypt it. DPAPI protects a particular Windows user's stored
secret; it is not a mechanism for safely distributing one secret to all users.
The hosted collector keeps credentials server-side and publishes only the bounded
aggregate snapshot for this addon. Its deployment and routing requirements are in
`deploy/census/README.md`. This does not grant permission to redistribute API data
elsewhere. Register the application for its actual hosted use and keep its policy
obligations current.

## Acceptance still needed

Run `python -m unittest discover -s backend -q` and `bash scripts/check.sh`.
For the focused addon pass use `OLYMPUS_TEST_FILTER=cloud bash scripts/check.sh`.
In game, verify login cleanup, both gamepad modes, manual import/clear and that
recruiting prepares a command without sending an invite or whisper autonomously.
Verify declined applicants are not repeatedly contacted. Live anti-cheat behavior
and Blizzard approval cannot be established by mocks or static checks.

Before enabling Forever collection, obtain the official namespace/endpoint
contract, update the explicit allowlist and tests, and validate the real target.
Do not switch US PvE targets to retail to suppress the disabled message.
