"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {compareCase, assertPinned, run} = require("./golden_compare.cjs");
const path = require("node:path");
const f = value => ({source: "SYNTHETIC", value});
const ap = Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(0)]));
const boosts = Object.fromEntries(["atk", "def", "spa", "spd", "spe"].map(k => [k, f(0)]));
const p = (species, ability) => ({species: f(species), level: f(50), nature: f("Serious"),
  ap, ability: f(ability), item: f(""), status: f(""), boosts});
const base = () => ({case_id: "SYN-GOLDEN-1", observed_at: "2026-10-31T00:00:00Z",
  regulation: "M-C", evidence: {origin: "SYNTHETIC", verification: "UNVERIFIED", references: []},
  checks: {}});
test("fixed SHA and synthetic stat / PP comparisons", () => {
  assertPinned();
  const c = base();
  c.checks.stats = {pokemon: p("Garchomp", "Rough Skin"), observed_stats:
    Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(1)]))};
  c.checks.pp = {move_id: f("Agility"), observed_battle_max_pp: f(20),
    observed_remaining_pp: {source: "UNKNOWN"}};
  const results = compareCase(c).checks;
  assert.equal(results.length, 7);
  assert.ok(results.slice(0, 6).every(r => r.status === "MISMATCH_SYNTHETIC"));
  assert.deepEqual(results.slice(0, 6).map(r => r.showdown), [183, 150, 115, 100, 105, 122]);
  assert.equal(results[6].status, "MATCH_SYNTHETIC");
  assert.equal(results[6].master_raw_pp, 30);
  assert.equal(results[6].showdown_capped_source_pp, 20);
  assert.equal(results[6].showdown_battle_max_pp, 20);
});
test("damage uses known conditions and 16 pinned-engine rolls", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"),
    defender: p("Hippowdon", "Sand Stream"), move_id: f("Earthquake"),
    defender_hp_before: f(100), defender_hp_after: f(70),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  const r = compareCase(c).checks[0];
  assert.equal(r.kind, "damage");
  assert.equal(r.rolls.length, 16);
  assert.ok(r.showdown[0] <= r.showdown[1]);
  c.checks.damage.defender_hp_after = f(100 - r.showdown[0]);
  assert.equal(compareCase(c).checks[0].status, "MATCH_SYNTHETIC");
  c.checks.damage.defender_hp_after = f(100);
  assert.equal(compareCase(c).checks[0].status, "MISMATCH_SYNTHETIC");
  c.checks.damage.field.defender_screen = f("reflect");
  const screened = compareCase(c).checks[0];
  assert.ok(screened.showdown[1] < r.showdown[1]);
  c.checks.damage.conditions_complete = {source: "UNKNOWN"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
});
test("nature modifies the displayed stat under the pinned Champions formula", () => {
  const c = base();
  c.checks.stats = {pokemon: p("Garchomp", "Rough Skin"), observed_stats:
    Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(1)]))};
  c.checks.stats.pokemon.nature = f("Jolly");
  const got = compareCase(c).checks;
  assert.equal(got.find(r => r.kind === "stats.spa").showdown, 90);
  assert.equal(got.find(r => r.kind === "stats.spe").showdown, 134);
});
test("unknown or inferred private inputs never become a match", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"),
    defender: p("Hippowdon", "Sand Stream"), move_id: f("Earthquake"),
    defender_hp_before: f(100), defender_hp_after: f(70),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  c.checks.damage.defender.item = {source: "UNKNOWN"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.damage.defender.item = {source: "INFERRED", value: "Leftovers"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.damage.defender.item = f("");
  c.checks.damage.field.terrain = f("electricterrain");
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
});
test("real-game facts require independent evidence references", () => {
  const c = base();
  c.evidence = {origin: "REAL_GAME", verification: "USER_VERIFIED",
    references: [{id: "frame-1", description: "local verified screenshot"}]};
  c.checks.pp = {move_id: f("Agility"), observed_battle_max_pp: f(20)};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.pp = {move_id: {source: "GAME_OBSERVED", value: "Agility", evidence_ids: ["frame-1"]},
    observed_battle_max_pp: {source: "GAME_OBSERVED", value: 20, evidence_ids: ["frame-1"]}};
  assert.equal(compareCase(c).checks[0].status, "MATCH");
});
test("CLI report keeps synthetic evidence outside verified game matches", () => {
  const report = run(path.join(__dirname, "synthetic_golden_example.json"));
  assert.deepEqual(report.counts, {match: 1, mismatch: 0, verified_game_match: 0,
    inconclusive: 0, synthetic: 1, unverified: 0});
  const c = base();
  c.evidence.verification = "USER_VERIFIED";
  c.checks.pp = {move_id: f("Agility"), observed_battle_max_pp: f(20)};
  assert.throws(() => compareCase(c), /synthetic case cannot be user verified/);
});
