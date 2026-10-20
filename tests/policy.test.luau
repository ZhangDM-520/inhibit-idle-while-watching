--!nonstrict
-- Pure tests for inhibit-idle-while-watching/policy.luau.
--     luau tests/policy.test.luau
-- The spawn policy is the plugin's whole safety story: never capture while
-- locked, never stack episodes, never exceed the camera wake-up budget. Every
-- branch is pinned here through injected inputs.

local policy = require("../inhibit-idle-while-watching/policy")

local failures = 0
local checks = 0

local function check(label, got, want)
  checks = checks + 1
  local ok
  if type(want) == "number" and type(got) == "number" then
    ok = math.abs(got - want) < 1e-6
  else
    ok = got == want
  end
  if not ok then
    failures = failures + 1
    print(string.format("FAIL  %s\n        got  %s\n        want %s",
      label, tostring(got), tostring(want)))
  end
end

local function decide(cfg, observed, state)
  return policy.decide(state or {}, cfg or {}, observed or {})
end

-- ── the master switch ───────────────────────────────────────────────────────
do
  local ok, reason = decide({ enabled = false }, { now_ms = 1000 })
  check("disabled: refused", ok, false)
  check("  ...named disabled", reason, "disabled")
end

do
  local ok, reason = decide({ enabled = "yes" }, { now_ms = 1000 })
  check("a wrong-typed switch falls back to the shipped default (on)", ok, true)
  check("  ...reason ok", reason, "ok")
end

-- ── guards ──────────────────────────────────────────────────────────────────
do
  local ok, reason = decide({}, { now_ms = 1000, locked = true })
  check("locked: refused", ok, false)
  check("  ...named locked", reason, "locked")
end

do
  local ok, reason = decide({}, { now_ms = 1000, episode_running = true })
  check("an episode already running: refused", ok, false)
  check("  ...named running", reason, "running")
end

do
  local ok, reason = decide({}, { now_ms = 1000, locked = false, episode_running = false })
  check("all clear: allowed", ok, true)
end

-- ── cooldown (camera wake-up budget) ────────────────────────────────────────
do
  local state = { last_start_ms = 1000 }
  local ok, reason = decide({}, { now_ms = 5000 }, state)
  check("inside the cooldown window: refused", ok, false)
  check("  ...named cooldown", reason, "cooldown")
  check("  ...the stamp is not refreshed by a refusal", state.last_start_ms, 1000)
end

do
  local state = { last_start_ms = 1000 }
  local ok = decide({ cooldown_s = 10 }, { now_ms = 11000 }, state)
  check("at the cooldown edge: allowed", ok, true)
  check("  ...and the stamp advances", state.last_start_ms, 11000)
end

do
  local state = { last_start_ms = 1000 }
  local ok = decide({ cooldown_s = "30" }, { now_ms = 12000 }, state)
  check("a wrong-typed cooldown falls back to the shipped default (10s)", ok, true)
  local state2 = { last_start_ms = 1000 }
  local ok2 = decide({ cooldown_s = 30 }, { now_ms = 12000 }, state2)
  check("  ...while a real 30s cooldown still refuses at 11s", ok2, false)
end

do
  local ok = decide({}, { now_ms = 1000 })
  check("first trigger ever: allowed", ok, true)
end

print(string.format("\n%d checks, %d failure(s)", checks, failures))
if failures > 0 then
  error(string.format("%d check(s) failed", failures), 0)
end
