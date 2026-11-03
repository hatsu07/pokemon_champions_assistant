"use strict";
// Synthetic contract tests. Not a real Showdown engine or an actual Battle.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const {auditPinnedChampionsRosterKeys, SOURCES} =
  require("./pinned_champions_roster_lookup.cjs");

const root = path.resolve(__dirname, "..");
function hash(raw) {
  return crypto.createHash("sha256").update(raw).digest("hex");
}
function fakeReport() {
  const source_sha256 = Object.entries(SOURCES).map(([kind, file]) =>
    [kind, hash(fs.readFileSync(path.join(root, file)))]);
  const members = Array.from({length: 6}, (_, i) => ({
    registered_name: "SYNTHETIC_MEMBER_" + i,
    proposed_base_key: "synthetic" + i,
    form_target: i === 3 ? "SYNTHETIC_MEGA_Z" : "",
    form_status: i === 3 ? "FORM_NOT_ENGINE_ATTESTED" : "NO_FORM_CHANGE_DECLARED",
    species_source_status: "IN_CHAMPIONS_USABLE_AND_LEARNSETS",
    moves: Array.from({length: 4}, (_, j) => ({
      slot: j + 1, key_candidate: "syntheticmove" + i + j,
      champions_move: "SYNTHETIC_MOVE_" + i + j,
      match_type: "EXACT_CHAMPIONS_NAME",
      learnset_membership: "IN_CHAMPIONS_LEARNSET",
    })),
  }));
  return {
    status: "CHAMPIONS_KEY_CANDIDATES_ONLY",
    source_sha256,
    team_sha256: source_sha256[0][1],
    members, candidate_species_keys: 6, candidate_move_keys: 24,
    expected_move_slots: 24, unmapped_slots: [],
    showdown_mapping_verified: false, champions_mechanics_verified: false,
    usable_in_live: false,
  };
}
function fakeDex({omit = null} = {}) {
  function get(id) {return {exists: id !== omit, id};}
  return {species: {get}, moves: {get}};
}
test("synthetic 6x4 source-hashed lookup is STILL not Champions approval", () => {
  const report = auditPinnedChampionsRosterKeys(fakeReport(), root, fakeDex());
  assert.equal(report.status, "PINNED_ENGINE_KEYS_EXIST_UNATTESTED");
  assert.equal(report.source_hashes_verified, true);
  assert.equal(report.species_found, 6);
  assert.equal(report.moves_found, 24);
  assert.deepEqual(report.unsupported_forms, ["SYNTHETIC_MEMBER_3"]);
  assert.equal(report.mapping_independently_attested, false);
  assert.equal(report.champions_mechanics_verified, false);
  assert.equal(report.executable_in_live, false);
  assert.equal(report.ranking_status, "NO_RANKING");
});
test("missing one engine entry never produces complete 30 key lookup", () => {
  const report = auditPinnedChampionsRosterKeys(
    fakeReport(), root, fakeDex({omit: "syntheticmove32"}));
  assert.equal(report.status, "PINNED_ENGINE_KEY_COVERAGE_INCOMPLETE");
  assert.deepEqual(report.moves_missing, ["SYNTHETIC_MEMBER_3.move3"]);
  assert.equal(report.moves_found, 23);
  assert.equal(report.executable_in_live, false);
});
test("wrong source SHA blocks before any engine metadata lookup", () => {
  const report = fakeReport();
  report.source_sha256[1][1] = "0".repeat(64);
  let attempted = false;
  const trap = () => {attempted = true; throw Error("must not use engine");};
  const result = auditPinnedChampionsRosterKeys(
    report, root, {species: {get:trap}, moves: {get:trap}});
  assert.equal(result.status, "NO_PINNED_ENGINE_ID_LOOKUP");
  assert.equal(result.reason, "SOURCE_MASTER_SHA_MISMATCH");
  assert.equal(result.source_hashes_verified, false);
  assert.equal(attempted, false);
});
test("forged approval, skipped slot or inferred Mega fail closed", () => {
  for (const mutate of [
    r => {r.usable_in_live = true;},
    r => {r.showdown_mapping_verified = true;},
    r => {r.members[0].moves.splice(0, 1);},
    r => {r.members[1].moves[0].slot = 3;},
    r => {r.members[2].moves[0].key_candidate = "not_a_key";},
    r => {r.members[3].form_status = "MEGA_Z_ENGINE_VERIFIED";},
    r => {r.members[5].species_source_status = "UNKNOWN";},
    r => {r.members[5].registered_name = r.members[4].registered_name;},
  ]) {
    const report = fakeReport(); mutate(report);
    const result = auditPinnedChampionsRosterKeys(report, root, fakeDex());
    assert.equal(result.status, "NO_PINNED_ENGINE_ID_LOOKUP", result.reason);
    assert.equal(result.species_found, 0);
    assert.equal(result.executable_in_live, false);
  }
});
test("wrong team SHA and duplicate manifest entry are rejected", () => {
  const wrongTeam = fakeReport();
  wrongTeam.team_sha256 = "a".repeat(64);
  assert.equal(auditPinnedChampionsRosterKeys(
    wrongTeam, root, fakeDex()).reason, "FROZEN_TEAM_SHA_MISMATCH");
  const duplicate = fakeReport();
  duplicate.source_sha256[3] = duplicate.source_sha256[0];
  assert.equal(auditPinnedChampionsRosterKeys(
    duplicate, root, fakeDex()).reason, "MALFORMED_OR_DUPLICATED_SOURCE_MANIFEST");
});
