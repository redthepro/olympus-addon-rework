local ns, test, eq, opt = ...
assert(loadfile(opt.ADDON_DIR .. "CloudCensus.lua"))("Olympus", ns)

local C = ns.CloudCensus
local NOW = 1800000000
local function Row(guild, fetched, modified, characters)
	return table.concat({ "OLYC1", "us", "profile-us", "realm", guild or "olympus", fetched or NOW,
		modified or 0, characters or 300 }, ";")
end

test("cloud census: retention deletes saved snapshots rather than only hiding them", function()
	local previousClock, previousDB = ns.Data.ServerTime, ns.db
	ns.Data.ServerTime = function() return NOW end
	ns.db = { cloudCensus = Row() }
	local ok, reason = pcall(function()
		C.Text(); assert(ns.db.cloudCensus)
		ns.Data.ServerTime = function() return NOW + 29 * 86400 end
		C.Text(); eq(ns.db.cloudCensus, nil)
		ns.Data.ServerTime = function() return NOW end
		eq(C.Import(Row() .. "\n" .. Row("old", NOW - 29 * 86400)), false)
		eq(ns.db.cloudCensus, nil)
	end)
	ns.Data.ServerTime, ns.db = previousClock, previousDB
	if not ok then error(reason, 0) end
end)

test("cloud census: bounded parser rejects malformed, duplicate, future and expired rows", function()
	local rows = assert(C.Parse(Row() .. "\r\n", NOW))
	eq(#rows, 1); eq(rows[1].characters, 300); eq(rows[1].modified, 0)
	for _, text in ipairs({ "", Row() .. "\n\n", Row() .. "\n" .. Row(), Row(nil, NOW + 61),
		Row(nil, NOW - 86401), Row(nil, NOW, NOW + 61), Row(nil, NOW, 0, 1001),
		Row():gsub("profile%-us", "profile-eu"),
		Row():gsub("OLYC1", "OLYC2"), Row():gsub("olympus", "olympus|cffffff"),
		Row():gsub(";300$", ";nan"), Row():gsub(";300$", ";-1"), string.rep("x", C.MAX_BYTES + 1) }) do
		eq(C.Parse(text, NOW), nil, "reject malformed import")
	end
	eq(C.Parse(Row(), nil), nil)
	local many = {}
	for i = 1, 257 do many[i] = Row("olympus-" .. i) end
	eq(C.Parse(table.concat(many, "\n"), NOW), nil)
end)

test("cloud census: import is atomic, unknown source stays unknown, live data is untouched", function()
	local previousClock, previousDB = ns.Data.ServerTime, ns.db
	local live = { Olympus = { total = 900, online = 50 } }
	ns.Data.ServerTime = function() return NOW end
	ns.db = { guilds = live }
	local ok, reason = pcall(function()
		assert(C.Import(Row()))
		local saved = ns.db.cloudCensus
		eq(C.Import(Row() .. "\ninvalid"), false)
		eq(ns.db.cloudCensus, saved, "invalid paste cannot replace last good baseline")
		eq(ns.db.guilds, live); eq(live.Olympus.total, 900); eq(live.Olympus.online, 50)
		local text = C.Text()
		assert(text:find("Source date unknown", 1, true))
		assert(text:find("Online: unavailable", 1, true))
		assert(text:find("not authenticated", 1, true))
		ns.Data.ServerTime = function() return NOW + 86401 end
		assert(C.Text():find("Saved snapshot unavailable", 1, true), "expired cache cannot claim current population")
		C.Clear(); eq(ns.db.cloudCensus, nil); eq(ns.db.guilds, live)
	end)
	ns.Data.ServerTime, ns.db = previousClock, previousDB
	if not ok then error(reason, 0) end
end)

test("cloud census: full scope is retained and a dated source is not refreshed by fetch", function()
	local rows = assert(C.Parse(Row("olympus", NOW, NOW - 90000), NOW))
	eq(rows[1].modified, NOW - 90000)
	eq(rows[1].namespace, "profile-us")
	eq(rows[1].key, "us/profile-us/realm/olympus")
	local distinct = assert(C.Parse(Row() .. "\n" .. Row():gsub(";realm;", ";another-realm;"), NOW))
	eq(#distinct, 2, "same guild name in different realm is distinct")
end)

test("cloud census: Forever profiles use explicit experimental ruleset or global scope", function()
	local global = Row():gsub(";profile%-us;realm;", ";profile-forever-us;global;")
	local rows = assert(C.Parse(global, NOW))
	eq(rows[1].realm, "global")
	local normal = global:gsub(";global;", ";normal;")
	eq(assert(C.Parse(normal, NOW))[1].realm, "normal")
	eq(C.Parse(global:gsub(";global;", ";realm;"), NOW), nil, "Forever never implies a realm")
	eq(C.Parse(Row():gsub(";realm;", ";global;"), NOW), nil, "retail cannot masquerade as global")
	eq(assert(C.Parse(Row():gsub(";realm;", ";normal;"), NOW))[1].realm, "normal", "retail realm named normal stays a realm")
	local previousClock, previousDB = ns.Data.ServerTime, ns.db
	ns.Data.ServerTime = function() return NOW end
	ns.db = {}
	local ok, reason = pcall(function()
		assert(C.Import(global))
		assert(C.Text():find("Global (experimental)", 1, true))
		assert(C.Text():find("Forever API compatibility awaiting live verification.", 1, true))
		assert(C.Import(normal))
		assert(C.Text():find("Normal ruleset (API-reported, experimental)", 1, true))
		assert(C.Import(Row():gsub(";realm;", ";normal;")))
		assert(not C.Text():find("Normal ruleset", 1, true), "retail realm must not be relabelled as a ruleset")
	end)
	ns.Data.ServerTime, ns.db = previousClock, previousDB
	if not ok then error(reason, 0) end
end)

test("cloud census: censusapi and legacy cloud commands reach the same snapshot actions", function()
	local show, paste, clear = C.Show, C.Paste, C.Clear
	local shown, pasted, cleared = 0, 0, 0
	C.Show = function() shown = shown + 1 end
	C.Paste = function() pasted = pasted + 1 end
	C.Clear = function() cleared = cleared + 1 end
	local ok, reason = pcall(function()
		SlashCmdList.OLYMPUS("censusapi")
		SlashCmdList.OLYMPUS("cloud status")
		SlashCmdList.OLYMPUS("censusapi paste")
		SlashCmdList.OLYMPUS("cloud clear")
		eq(shown, 3); eq(pasted, 1); eq(cleared, 1)
	end)
	C.Show, C.Paste, C.Clear = show, paste, clear
	if not ok then error(reason, 0) end
end)

local function WithClock(fn)
	local previousClock, previousDB = ns.Data.ServerTime, ns.db
	ns.Data.ServerTime = function() return NOW end
	ns.db = {}
	local ok, reason = pcall(fn)
	ns.Data.ServerTime, ns.db = previousClock, previousDB
	if not ok then error(reason, 0) end
end

test("cloud census regression: older fetch or source and equal-date conflicts cannot overwrite newer guilds", function()
	WithClock(function()
		assert(C.Import(Row("olympus", NOW, NOW - 300, 300)))
		local saved = ns.db.cloudCensus
		for _, older in ipairs({ Row("olympus", NOW - 1, NOW - 300, 300),
			Row("olympus", NOW, NOW - 301, 300), Row("olympus", NOW, 0, 300),
			Row("olympus", NOW, NOW - 300, 299),
			Row("olympus", NOW - 86401) .. "\n" .. Row("olympus-ii", NOW) }) do
			eq(C.Import(older), false, "regression must be rejected")
			eq(ns.db.cloudCensus, saved, "rejected update leaves complete snapshot unchanged")
		end
		assert(C.Import(Row("olympus", NOW, NOW - 300, 300)), "identical repeat remains idempotent")
		assert(C.Import(Row("olympus", NOW, NOW - 299, 299)), "newer source may legitimately decrease population")
		eq(assert(C.Parse(ns.db.cloudCensus, NOW))[1].characters, 299)
		local latest = ns.db.cloudCensus
		ns.Data.ServerTime = function() return NOW + 86401 end
		eq(C.Import(Row("olympus", NOW + 86401, NOW - 300, 300)), false,
			"an expired saved baseline still blocks regressed source evidence behind a fresh fetch")
		eq(ns.db.cloudCensus, latest)
	end)
end)

test("cloud census regression: expiry is per guild and cannot hide a still-current snapshot", function()
	WithClock(function()
		local mixed = Row("olympus", NOW - 86399) .. "\n" .. Row("olympus-ii", NOW)
		assert(C.Import(mixed))
		ns.Data.ServerTime = function() return NOW + 2 end
		local report = C.Text()
		assert(report:find("olympus-ii - 300 characters", 1, true), "fresh guild remains visible when its sibling expires")
		assert(not report:find("olympus - 300 characters", 1, true), "expired guild's count is withheld")
		assert(report:find("1 expired guild snapshot", 1, true), "partial coverage is explicit")
		local rows = assert(C.Parse(mixed, NOW + 2))
		eq(#rows, 1)
		eq(rows[1].guild, "olympus-ii")
	end)
end)

test("cloud census regression: nonfinite clock and oversized expired-row batches are rejected", function()
	eq(C.Parse(Row(), 0 / 0), nil, "NaN must not bypass temporal checks")
	eq(C.Parse(Row(), math.huge), nil)
	local many = {}
	for i = 1, 256 do many[i] = Row("expired-" .. i, NOW - 86401) end
	many[#many + 1] = Row("olympus", NOW)
	eq(C.Parse(table.concat(many, "\n"), NOW), nil, "expired rows count against the transport limit")
	eq(C.Parse(Row("expired", NOW - 86401) .. "\ninvalid", NOW), nil, "expiry cannot hide malformed rows")
end)

test("cloud census regression: a maximum-size export survives Windows clipboard CRLF", function()
	local lines = {}
	local realm = string.rep("r", 80)
	for i = 1, 256 do
		local guild = "guild" .. i
		local base = Row(guild):gsub(";realm;", ";" .. realm .. ";")
		guild = guild .. string.rep("g", 191 - #base)
		lines[i] = Row(guild):gsub(";realm;", ";" .. realm .. ";")
	end
	local unix = table.concat(lines, "\n")
	eq(#unix, C.MAX_BYTES - 1)
	eq(#assert(C.Parse(unix, NOW)), 256)
	local windows = unix:gsub("\n", "\r\n") .. "\r\n"
	assert(#windows > C.MAX_BYTES)
	eq(#assert(C.Parse(windows, NOW)), 256, "line-ending conversion must not invalidate a valid bounded export")
end)

test("cloud census baseline: scoped API membership changes display only, preserves live presence and ignores unknown or old sources", function()
	local saved = { ns.db, ns.realm, ns.group, ns.Data.ServerTime, ns.faction }
	ns.realm, ns.group, ns.faction = "CAMWOWUS1Realm1PvP", "ForeverPvP", "Alliance"
	ns.Data.ServerTime = function() return NOW end
	local function Wire(scope, modified)
		return table.concat({"OLYC2", "us", "profile-forever-us", scope, "olympus", NOW, modified, 200, "A"}, ";")
	end
	local original = { characters=100, total=90, alts=10, online=17, zones={city=17},
		guilds={{name="Olympus", counted=true, fresh=true, g={total=100, online=17, leader="Lord", t=NOW}}} }
	ns.db = {cloudCensus=Wire("pvp", NOW-3600)}
	local result = C.Summary(original)
	eq(result.characters, 200); eq(result.total, 200); eq(result.alts, 0)
	eq(result.online, 17); eq(result.zones, original.zones)
	eq(result.guilds[1].g.total, 200); eq(result.guilds[1].g.leader, "Lord")
	eq(original.characters, 100); eq(original.guilds[1].g.total, 100, "underlying report untouched")
	original.characters, original.total, original.snapshot = 90000, 80000, {characters=90000}
	eq(C.Summary(original).characters, 200, "retained aggregate cannot double-count API rows")
	original.guilds[1].g.total = 5000
	eq(C.Summary(original).characters, 200, "replacement uses the same cap as the original census")
	original.guilds[1].g.total = 100
	local sightings = {characters=0,total=0,online=0,guilds={},seen={{name="Olympus",online=5}}}
	local added = C.Summary(sightings)
	eq(added.characters,200); eq(#added.guilds,1); eq(#added.seen,0, "API-only guild is not duplicated as a who-only sighting")
	eq(#sightings.seen,1, "original sightings are untouched")
	for _, input in ipairs({Wire("pvp", NOW):gsub(";A$", ";H"), Wire("pvp", NOW):gsub("OLYC2", "OLYC1"):gsub(";A$", ""), Wire("normal", NOW), Wire("global", NOW), Wire("pvp", 0), Wire("pvp", NOW-86401)}) do
		ns.db.cloudCensus=input; eq(C.Summary(original), original, "inapplicable baseline excluded")
	end
	ns.db, ns.realm, ns.group, ns.Data.ServerTime, ns.faction = unpack(saved)
end)
