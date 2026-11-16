# Blizzard API census snapshots

**Current restriction:** Forever API requests are disabled until official
Blizzard documentation establishes the endpoint contract. The live addon census
continues to work. The instructions below describe the import mechanism and
future Forever acceptance checks, not an enabled Forever collection service.
Read [policy review and data deletion](BLIZZARD-POLICY.md), including the 29-day
cache lifetime and responsibility to delete inactive copies/backups.

The Census window's **API census** button (or `/oly censusapi`) opens the
**Blizzard roster snapshot** view. **Paste snapshot** accepts the snapshot copied
from the local Olympus desktop companion. `/oly censusapi paste` opens the editor
directly; `/oly censusapi clear` removes the saved snapshot. The original `/oly cloud`
commands remain aliases. The normal Help window still has Report a bug.

The desktop companion runs on the player's computer and uses Blizzard's API to
refresh its local database. No hosted database, RDS instance, router port forwarding,
or in-game network connection is required for this workflow. Blizzard controls when
its source rosters update, so this is a delayed roster snapshot rather than live
online presence. Copy the latest available snapshot in the companion and paste it
into the addon when needed. API credentials stay outside the addon.

The addon cannot make HTTP requests. This first transport is a deliberate manual
copy/paste; it is not live synchronization, a Blizzard login, or an authenticated
assertion. The import displays each region, namespace and realm explicitly. It
does not guess which API realm corresponds to the player's current Forever realm.

The main Census uses imported membership counts for matching Forever region and ruleset
with matching faction, when both fetch and known source dates are within 24 hours. Other guilds retain live-report
membership counts. This is a sum of independently dated observations, not a simultaneous
census; the Census labels API coverage. Online counts still come from live reports. Unknown
source dates and global/unmatched scopes remain visible only in the API report.
The display overlay never changes underlying reports, relay observations, guild verification,
permissions, or in-game messages. Alt deductions are withheld in the overlaid character total. Counts are characters, not people. Fetch age and source age
are shown separately; absent source dates remain unknown. A saved export more than
24 hours past its fetch is withheld; fresh guilds in the same saved snapshot remain
visible, with the number of withheld rows shown. **Clear snapshot** removes it.

The editor and report are Olympus-owned windows available in either input mode.
They never take keyboard focus automatically or use Blizzard's native popups.
The existing gamepad gate disables slash commands under gamepad; use the Census
button there.

## Export contract

One ASCII line per guild:

```
OLYC2;region;namespace;scope;guild-slug;fetched-epoch;source-modified-epoch-or-0;characters;A-or-H
```

Legacy OLYC1 rows without faction remain readable in the API report but do not feed the main census. Faction-scoped Forever exports use OLYC2.

At most 256 rows and 49,152 bytes after line-ending normalization. CRLF and a single
trailing newline are accepted, including Windows clipboard conversion of a large export.
Regions are `us`, `eu`, `kr`, `tw`. Namespace (64 bytes), realm (80) and guild (96)
are lowercase ASCII alphanumeric/hyphen slugs without leading, trailing or repeated
hyphens. Namespace must start `profile-` and end in the row's region. For exact
`profile-forever-{region}` namespaces, the scope field must be `normal`, `pvp`, `roleplay`, `roleplay-pvp` or `global`;
other namespaces require a realm slug and reject `global` (a retail realm named
`normal` is still a realm). Ruleset scope is emitted by the companion only after
Blizzard's guild summary identifies the selected ruleset and its guild ID matches
the roster. The addon cannot authenticate pasted text itself. Forever imports display
**Normal ruleset (API-reported, experimental)** or **Global (experimental)**, and
**Forever API compatibility awaiting live verification**.
This representation does not claim the snapshot belongs to the current player's
realm or establish that a live Blizzard API request succeeds.
Timestamps are nonnegative integers at most 9,999,999,999. Fetch must be nonzero,
within the past 24 hours and no more than 60 seconds ahead of server time. Source
modified must be zero (unknown) or no more than 60 seconds after fetch. Counts are
integers 0 through 1,000. Duplicate region/namespace/realm/guild keys invalidate
the entire import. Expired guild rows are withheld only after validating all rows;
an entirely expired import is rejected. Malformed imports preserve the last good
snapshot. An import with older fetch/source dates or conflicting counts at the
same source date cannot replace a matching saved guild. Import replaces the
snapshot as a whole; omitted guilds are removed. Clear explicitly to reset a baseline.

## In-game verification still required

1. Open Census, then API census, in mouse and gamepad modes. Copy a snapshot from
   the desktop companion, paste it in the editor, and confirm the scope and
   separate date labels. Both `/oly censusapi` and `/oly cloud` open this view in
   mouse mode.
2. Confirm ordinary census totals and permissions remain unchanged.
3. Paste invalid, duplicate, oversized, entirely expired and regressed exports;
   confirm rejection and retention of the last valid baseline. With a mixed-age
   snapshot, confirm only fresh guilds appear and expired rows are acknowledged.
4. Reload and confirm the baseline persists; clear it, reload and confirm removal.
5. Switch input modes with editor/report open; confirm no native popup, unsolicited
   focus change, or blocked Blizzard action.

## Recruiting feedback and acceptance

Players reported negative feedback that invite handoffs pass invites off to other members and spam people. Referral sending, receiving, availability probes, automatic overflow, per-request Pass actions and their Church switches have been removed. Legacy saved acceptHandoffs/offerOverflow opt-ins are cleared on restore. Legacy J4 packets receive no reply. Local candidate discovery, JOIN handling and the existing attended invite flow remain.

In game: enable Recruiting in Church > Invites; verify there is no handoff section or Pass button. Receive JOIN requests past the invite limit: they must stay local until the limit clears, without probing guildmates. Reload with old saved handoff opt-ins and verify the options remain absent. Test with an older client sending J4 offers: no claim popup or response should occur.

US PvE uses profile-forever-us and the normal ruleset. Run companion/Setup-Companion.cmd with your developer credentials, add the exact official guild slugs to settings.json, then companion/Start-Companion.cmd. Confirm guild summary ruleset and roster identity, prepare/copy the snapshot and import through Census > API census. With source dates within 24 hours, the main Census should show API coverage and updated membership; online and location must remain live. Global, other-region/ruleset, unknown-source and expired-source rows must not change the main count. Clear the snapshot to restore the live-report display. Credentials are never pasted into WoW.
