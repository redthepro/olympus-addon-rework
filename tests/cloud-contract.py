"""Exercise the real SQLite -> Python export -> Lua addon import boundary offline."""
from email.utils import formatdate
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from service import Store
from webapp import export_snapshot


NOW = 1800000000


def roster(guild_id, name, ids):
    return {
        'guild': {'id': guild_id, 'name': name,
                  'realm': {'id': 1, 'slug': 'realm'}, 'faction': {'type': 'ALLIANCE'}},
        'members': [{'character': {'id': identity, 'realm': {'id': 1},
                                  'name': 'Fixture-' + str(identity)}} for identity in ids],
    }


def main():
    luajit = shutil.which('luajit')
    if not luajit:
        raise SystemExit('LuaJIT is required for the cross-language census contract check.')
    with tempfile.TemporaryDirectory(prefix='olympus-contract-') as directory:
        temp = Path(directory)
        store = Store(str(temp / 'census.sqlite3'))
        with patch('service.time.time', return_value=NOW):
            store.save('us/profile-us/realm/olympus-i',
                       roster(8, 'Olympus I', [1, 1, 2]), formatdate(NOW - 3600, usegmt=True))
            store.save('us/profile-us/realm/olympus-ii',
                       roster(9, 'Olympus II', [3]))
            # Synthetic realmless adapter fixture, NOT a captured Blizzard response.
            # Matches the independently documented Forever guild/character shapes.
            forever_payload = {
                '_links': {'self': {'href': 'https://us.api.blizzard.com/data/wow/guild/olympus/roster?namespace=profile-forever-us'}},
                'guild': {'id': 80, 'name': 'Olympus', 'slug': 'olympus',
                          'faction': {'type': 'ALLIANCE'}},
                'members': [{'character': {'id': 123, 'name': 'Test', 'surname': 'Example',
                                          'slug': 'test-example', 'level': 5,
                                          'playable_class': {'id': 1}}, 'rank': 0}],
            }
            store.save('us/profile-forever-us/global/olympus', forever_payload)
            # The live collector adds this evidence only after the independent guild
            # summary names Normal and matches this roster's guild identity.
            normal_payload = dict(forever_payload, _olympus_ruleset={
                'guild_id': 80, 'guild_slug': 'olympus', 'id': 123, 'name': 'Normal'})
            store.save('us/profile-forever-us/normal/olympus', normal_payload)
            exported = export_snapshot(store.census(), now=NOW)
        snapshot = temp / 'snapshot.txt'
        snapshot.write_text(exported, encoding='ascii')
        harness = temp / 'contract.lua'
        harness.write_text('''
local source = assert(io.open(arg[1], "rb"))
local text = source:read("*a"); source:close()
local now = 1800000000
local live = { Olympus = { total = 500, online = 25 } }
local ns = { db = { guilds = live }, Data = { ServerTime = function() return now end } }
assert(loadfile(arg[2]))("Olympus", ns)
local cloud = assert(ns.CloudCensus)
local rows = assert(cloud.Parse(text, now))
assert(#rows == 4, "all database snapshots reach Lua")
local byKey = {}
for _, row in ipairs(rows) do byKey[row.key] = row end
local first = assert(byKey["us/profile-us/realm/olympus-i"])
local second = assert(byKey["us/profile-us/realm/olympus-ii"])
local forever = assert(byKey["us/profile-forever-us/global/olympus"])
local normal = assert(byKey["us/profile-forever-us/normal/olympus"])
assert(first.characters == 2, "database deduplication survives export")
assert(first.fetched == now and first.modified == now - 3600, "source age survives fresh fetch")
assert(second.characters == 1 and second.modified == 0, "unknown source is preserved")
assert(forever.characters == 1 and forever.realm == "global", "realmless Forever scope survives export")
assert(normal.characters == 1 and normal.realm == "normal", "verified Normal ruleset scope survives export")
assert(cloud.Import(text))
assert(ns.db.guilds == live and live.Olympus.online == 25, "no live state overwrite")
local saved = ns.db.cloudCensus
local firstLine = text:match("[^\\n]+")
assert(not cloud.Import(text .. "\\n" .. firstLine), "duplicate export rejected atomically")
assert(ns.db.cloudCensus == saved)
assert(not cloud.Parse(text, now + 86401), "expired export rejected")
local report = cloud.Text()
assert(report:find("Source age: 60 min (old)", 1, true))
assert(report:find("Source date unknown", 1, true))
assert(report:find("Global (experimental)", 1, true))
assert(report:find("Normal ruleset (API-reported, experimental)", 1, true))
assert(report:find("Forever API compatibility awaiting live verification.", 1, true))
print("Census cross-language contract: SQLite -> Python export -> Lua import passed")
''', encoding='utf-8')
        subprocess.run([luajit, str(harness), str(snapshot),
                        str(ROOT / 'Olympus' / 'CloudCensus.lua')], check=True, cwd=ROOT)


if __name__ == '__main__':
    main()
