local ADDON, ns = ...

-- A manually imported Blizzard API snapshot from the local desktop companion. Never a writer to Data, Comm, permissions or the
-- live online count. This copyable transport works inside WoW's network sandbox.
local Cloud = {}
ns.CloudCensus = Cloud
Cloud.MAX_BYTES, Cloud.MAX_ROWS, Cloud.MAX_AGE = 49152, 256, 86400

local function Clock()
	local value = ns.Data and ns.Data.ServerTime and ns.Data.ServerTime()
	if type(value) == "number" and value > 0 and value <= 9999999999 then return math.floor(value) end
end

local function Integer(text, maximum)
	if type(text) ~= "string" or not text:match("^%d+$") or #text > 10 then return nil end
	local n = tonumber(text)
	if not n or n > maximum then return nil end
	return n
end

local function Slug(text, maximum)
	return type(text) == "string" and #text > 0 and #text <= maximum
		and text:match("^[a-z0-9][a-z0-9%-]*$") and not text:find("%-%-") and text:sub(-1) ~= "-"
end

local REGIONS = { us = true, eu = true, kr = true, tw = true }
-- Parsing produces a fresh table; invalid input never replaces the last good snapshot.
local function ParseSnapshot(text, now, keepExpired)
	-- Windows clipboard conversion can add one CR per row and a final CRLF. Bound
	-- that small overhead before normalization, then enforce the wire byte limit.
	if type(text) ~= "string" or #text == 0 or #text > Cloud.MAX_BYTES + Cloud.MAX_ROWS + 2 then return nil, "Empty or oversized snapshot." end
	if type(now) ~= "number" or now ~= now or now <= 0 or now > 9999999999 then return nil, "Server time is unavailable. Try again after login." end
	text = text:gsub("\r\n", "\n"):gsub("\n$", "")
	if #text > Cloud.MAX_BYTES then return nil, "Empty or oversized snapshot." end
	local rows, seen, scanned, expired = {}, {}, 0, 0
	for line in (text .. "\n"):gmatch("(.-)\n") do
		scanned = scanned + 1
		if scanned > Cloud.MAX_ROWS then return nil, "Too many guilds." end
		local faction
		local body, code = line:match("^OLYC2;(.+);([AH])$")
		if body then line, faction = "OLYC1;" .. body, code == "A" and "Alliance" or "Horde" end
		local region, namespace, realm, guild, fetched, modified, characters =
			line:match("^OLYC1;([^;]+);([^;]+);([^;]+);([^;]+);([^;]+);([^;]+);([^;]+)$")
		if not REGIONS[region] or not Slug(namespace, 64) or not namespace:match("^profile%-")
			or namespace:sub(-#region - 1) ~= "-" .. region or not Slug(realm, 80) or not Slug(guild, 96) then
			return nil, "Invalid version or scope. Copy the complete snapshot from the desktop companion."
		end
		local forever = namespace == "profile-forever-" .. region
		if (forever and realm ~= "global" and realm ~= "normal" and realm ~= "pvp" and realm ~= "roleplay" and realm ~= "roleplay-pvp") or (not forever and realm == "global") then
			return nil, "Forever snapshots require Normal or global scope; other profiles require a realm."
		end
		fetched, modified, characters = Integer(fetched, 9999999999), Integer(modified, 9999999999), Integer(characters, 1000)
		if not fetched or fetched == 0 or not modified or not characters or fetched > now + 60 or modified > fetched + 60 then
			return nil, "Invalid count or date."
		end
		local key = table.concat({ region, namespace, realm, guild }, "/")
		if seen[key] then return nil, "Duplicate guild in the same scope." end
		seen[key] = true
		local old = now - fetched > Cloud.MAX_AGE
		if old then expired = expired + 1 end
		if not old or keepExpired then
			rows[#rows + 1] = { key = key, region = region, namespace = namespace, realm = realm, guild = guild,
				fetched = fetched, modified = modified, characters = characters, faction = faction }
		end
	end
	if #rows == 0 then return nil, "No current guild snapshots. Fetch a new copy in the companion." end
	table.sort(rows, function(a, b) return a.key < b.key end)
	return rows, nil, expired
end

function Cloud.Parse(text, now)
	return ParseSnapshot(text, now, false)
end

-- Delete expired saved data, rather than merely excluding it from display.
-- Run on login and periodically, even if the census window is never opened.
function Cloud.Purge()
	local now = Clock()
	if not now or not ns.db or ns.db.cloudCensus == nil then return end
	local rows = ParseSnapshot(ns.db.cloudCensus, now, true)
	if not rows then ns.db.cloudCensus = nil; return end
	for _, row in ipairs(rows) do
		if now - row.fetched >= 29 * 86400 then
			-- Discard the complete manual import if any constituent reaches its TTL.
			ns.db.cloudCensus = nil
			return
		end
	end
end

ns.On("LOGIN", function()
	Cloud.Purge()
	ns.Every(60, "API snapshot retention", Cloud.Purge)
end)

function Cloud.Import(text)
	Cloud.Purge()
	local now = Clock()
	local rows, reason, expired = Cloud.Parse(text, now)
	if not rows then return false, reason end
	if type(ns.db) ~= "table" then return false, "Saved settings are not ready." end
	-- Source dates are evidence, while fetch dates are transport. A fresh fetch cannot
	-- make an older source new; even an expired saved row protects against that rollback.
	local held = ParseSnapshot(ns.db.cloudCensus, now, true)
	local byKey = {}
	for _, row in ipairs(held or {}) do byKey[row.key] = row end
	-- Compare expired input too: dropping its displayed row must not let an old
	-- export remove a newer saved copy of that same guild.
	local incoming = ParseSnapshot(text, now, true)
	for _, row in ipairs(incoming) do
		if now - row.fetched >= 29 * 86400 then return false, "Snapshot exceeds the retention limit." end
		local previous = byKey[row.key]
		if previous and (row.fetched < previous.fetched or row.modified < previous.modified
			or ((row.modified == previous.modified and (row.modified > 0 or row.fetched == previous.fetched))
				and row.characters ~= previous.characters)) then
			return false, "Older or conflicting snapshot for " .. row.guild .. ". Fetch a new copy in the companion."
		end
	end
	-- Keep only bounded text, so saved values are revalidated whenever displayed.
	ns.db.cloudCensus = text
	if ns.Data and ns.Data.ChangedNow then ns.Data.ChangedNow() end
	return true, "Imported " .. #rows .. " guild baselines." .. (expired > 0 and (" Withheld " .. expired .. " expired guild snapshots.") or "")
end

function Cloud.Clear()
	if ns.db then ns.db.cloudCensus = nil end
	if ns.Data and ns.Data.ChangedNow then ns.Data.ChangedNow() end
end

function Cloud.Text()
	Cloud.Purge()
	local lines = {
		"Blizzard roster snapshot - manually imported; not authenticated by the addon.",
		"Shared census: https://olympus-v.org/census",
		"Members using the shared page do not need API credentials or the desktop companion.",
		"Forever API collection is currently disabled pending official documentation; live census remains available.",
		"Matching Forever region/ruleset counts feed Census membership; live presence stays separate.",
		"Characters are roster entries, not unique people. No live online counts or guild permissions are supplied.",
		"Ages are checked when this report opens. Reopen API census to refresh them.",
		"", "Copy a snapshot from the guild website or optional local companion, then choose Paste snapshot here.", "",
	}
	local raw = ns.db and ns.db.cloudCensus
	if raw == nil then lines[#lines + 1] = "No snapshot imported."; return table.concat(lines, "\n") end
	local now = Clock()
	local rows, reason, expired = Cloud.Parse(raw, now)
	if not rows then
		lines[#lines + 1] = "Saved snapshot unavailable: " .. reason
		return table.concat(lines, "\n")
	end
	if expired > 0 then
		lines[#lines + 1] = expired .. " expired guild snapshot(s) withheld; the guilds below remain available."
		lines[#lines + 1] = ""
	end
	for _, row in ipairs(rows) do
		local fetchAge = math.max(0, now - row.fetched)
		local source = row.modified == 0 and "Source date unknown" or
			("Source age: %d min%s"):format(math.floor(math.max(0, now - row.modified) / 60), now - row.modified > 1800 and " (old)" or "")
		lines[#lines + 1] = row.guild .. " - " .. row.characters .. " characters"
		local forever = row.namespace == "profile-forever-" .. row.region
		local scope = forever and (row.realm ~= "global" and ((({ normal="Normal", pvp="PvP", roleplay="Roleplay", ["roleplay-pvp"]="Roleplay PvP" })[row.realm] or row.realm) .. " ruleset (API-reported, experimental)") or "Global (experimental)") or row.realm
		lines[#lines + 1] = row.region .. " / " .. row.namespace .. " / " .. scope .. " / " .. (row.faction or "faction unspecified; report only")
		if forever then
			lines[#lines + 1] = "Forever API compatibility awaiting live verification."
		end
		lines[#lines + 1] = ("Fetched %d min ago%s. %s. Online: unavailable."):format(math.floor(fetchAge / 60), fetchAge > 1800 and " (old fetch)" or "", source)
		lines[#lines + 1] = ""
	end
	return table.concat(lines, "\n")
end

-- Retain the existing layout and text wrapping while keeping every control on small
-- game viewports. Scaling returns to normal when reopened in a larger viewport.
local function FitWindow(frame, width, height) -- gp:cloud-census
	if not ns.Gate.Allowed("cloud-census") then return end
	local availableWidth, availableHeight = UIParent:GetWidth(), UIParent:GetHeight()
	local scale = math.min(1, math.max(1, availableWidth - 20) / width, math.max(1, availableHeight - 20) / height)
	frame:SetScale(scale)
	frame:SetClampedToScreen(true)
end

local editor
function Cloud.Paste() -- gp:cloud-census
	if not ns.Gate.Allowed("cloud-census") then return end
	if not editor then
		local f = ns.Window("OlympusCloudCensusPaste", nil, {})
		f:SetSize(620, 390)
		f:SetPoint("CENTER")
		f:SetFrameStrata("DIALOG")
		ns.SetWindowTitle(f, "Paste Blizzard roster snapshot")
		local hint = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
		hint:SetPoint("TOPLEFT", 16, -36)
		hint:SetWidth(580)
		hint:SetText("Paste the complete export. These are imported roster counts, not live online counts.")
		local scroll = CreateFrame("ScrollFrame", nil, f, "UIPanelScrollFrameTemplate")
		scroll:SetPoint("TOPLEFT", 16, -64)
		scroll:SetPoint("BOTTOMRIGHT", -34, 65)
		local box = CreateFrame("EditBox", nil, scroll)
		box:SetMultiLine(true)
		box:SetFontObject("GameFontHighlightSmall")
		box:SetWidth(560)
		box:SetHeight(220)
		box:SetAutoFocus(false)
		-- One byte beyond the parser limit so oversized pastes cannot silently become valid.
		box:SetMaxLetters(Cloud.MAX_BYTES + Cloud.MAX_ROWS + 3)
		box:SetScript("OnEscapePressed", function() -- gp:cloud-census
			if not ns.Gate.Allowed("cloud-census") then return end
			f:Hide()
		end)
		scroll:SetScrollChild(box)
		f.box = box
		local result = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
		result:SetPoint("BOTTOMLEFT", 16, 34)
		result:SetWidth(580)
		f.result = result
		local button = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
		button:SetSize(140, 22)
		button:SetPoint("BOTTOMLEFT", 16, 8)
		button:SetText("Import baseline")
		button:SetScript("OnClick", function() -- gp:cloud-census
			if not ns.Gate.Allowed("cloud-census") then return end
			local ok, message = Cloud.Import(box:GetText())
			result:SetText(message)
			if ok then f:Hide(); Cloud.Show() end
		end)
		f.import = button
		f:SetScript("OnHide", function() -- gp:cloud-census
			if not ns.Gate.Allowed("cloud-census") then return end
			box:SetText("")
			box:ClearFocus()
		end)
		editor = f
	end
	editor.box:SetText("")
	editor.result:SetText("")
	FitWindow(editor, 620, 390)
	editor:Show()
	return editor
end

function Cloud.Show() -- gp:cloud-census
	if not ns.Gate.Allowed("cloud-census") then return end
	local report = ns.UI.ShowCopy("Blizzard roster snapshot", Cloud.Text(), { label = "Paste snapshot", fn = function() Cloud.Paste() end },
		{ key = "cloudCensus", big = true, auto = true, second = { label = "Clear snapshot", fn = function()
			Cloud.Clear(); Cloud.Show()
		end } })
	if report then FitWindow(report, 680, 460) end
	return report
end

function Cloud.Slash(input)
	local command = (input or ""):lower()
	if command == "paste" then return Cloud.Paste() end
	if command == "clear" then Cloud.Clear() end
	return Cloud.Show()
end

-- Display-only overlay: imported membership never becomes a reporter vote, a relay
-- observation, guild verification, or permission evidence.
function Cloud.Summary(s)
	Cloud.Purge()
	local now = Clock()
	if not now then return s end
	local region = type(ns.realm) == "string" and ns.realm:match("^CAMWOW(%u%u)%d+Realm")
	local scope = ({ ForeverPvE = "normal", ForeverPvP = "pvp", ForeverRP = "roleplay", ForeverRPPvP = "roleplay-pvp" })[ns.group]
	if not region or not scope then return s end
	local rows = Cloud.Parse(ns.db and ns.db.cloudCensus, now)
	if not rows then return s end
	local function SlugOf(name) return name:lower():gsub("%s+", "-") end
	local baselines, n = {}, 0
	for _, row in ipairs(rows) do
		-- Unknown/old source dates remain visible in API census, outside the main total.
		if row.faction == ns.faction and row.region == region:lower() and row.realm == scope and row.namespace == "profile-forever-" .. row.region
			and row.modified > 0 and now - row.modified <= Cloud.MAX_AGE then
			baselines[row.guild], n = row, n + 1
		end
	end
	if n == 0 then return s end
	local out = {}; for k, v in pairs(s) do out[k] = v end
	out.guilds = {}; out.api = { guilds = 0 }
	-- A retained aggregate may cover guilds absent from the current row list.
	-- Rebuild from explicit rows before overlaying, rather than adding into that aggregate twice.
	local characters = 0
	for _, e in ipairs(s.guilds or {}) do
		if e.counted then characters = characters + math.min(e.g.total or 0, ns.Codec.GUILD_CAP) end
	end
	for _, e in ipairs(s.guilds or {}) do
		local row = baselines[SlugOf(e.name)]
		local copy = {}; for k, v in pairs(e) do copy[k] = v end
		if row then
			local g = {}; for k, v in pairs(e.g) do g[k] = v end
			characters = characters - (e.counted and math.min(g.total or 0, ns.Codec.GUILD_CAP) or 0) + row.characters
			g.total, g.apiFetched, g.apiModified = row.characters, row.fetched, row.modified
			copy.g, copy.counted = g, true
			out.api.guilds = out.api.guilds + 1
			baselines[row.guild] = nil
		end
		out.guilds[#out.guilds + 1] = copy
	end
	for slug, row in pairs(baselines) do
		local name = slug:gsub("-", " ")
		if ns.IsFederation(name) and not ns.Data.NetOff(name) then
			out.guilds[#out.guilds + 1] = { name = name, counted = true, fresh = false, observed = row.modified,
				g = { total = row.characters, online = 0, t = row.modified, st = row.modified, zones = {},
					classes = {}, apiFetched = row.fetched, apiModified = row.modified, relayed = true } }
			characters = characters + row.characters; out.api.guilds = out.api.guilds + 1
		end
	end
	if out.api.guilds == 0 then return s end
	local listed = {}; for _, e in ipairs(out.guilds) do listed[SlugOf(e.name)] = true end
	out.seen = {}
	for _, e in ipairs(s.seen or {}) do
		if not listed[SlugOf(e.name)] then out.seen[#out.seen + 1] = e end
	end
	out.characters, out.total, out.alts = characters, characters, 0
	-- Alt links cannot reliably be applied across independently dated API rosters.
	out.snapshot, out.live = nil, nil
	table.sort(out.guilds, ns.Data.GuildDefaultLess)
	return out
end
