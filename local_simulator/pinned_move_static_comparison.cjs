"use strict";
/**
 * #141 Stage1: compare exact 6x24 fixed Champions static move records with
 * the pinned Showdown Champions Dex. This is a *metadata* equivalence check,
 * NOT a comparison of move effects, Mega-Z, legality, accuracy RNG or actual
 * in-game behavior. The results are never executable recommendations.
 */
const HEX = /^[0-9a-f]{64}$/;
const ID = /^[a-z0-9]+$/;
const TYPES = new Set([
  "Normal","Fire","Water","Electric","Grass","Ice","Fighting","Poison",
  "Ground","Flying","Psychic","Bug","Rock","Ghost","Dragon","Dark",
  "Steel","Fairy",
]);
const CATEGORIES = new Set(["Physical","Special","Status"]);

function refused(reason) {
  return Object.freeze({
    status:"PINNED_STATIC_CHECK_REFUSED", reason, checked_slots:0,
    equal_slots:0, mismatched:[], missing:[],
    game_mechanics_verified:false, ready_for_live:false,
    ranking_status:"NO_RANKING",
  });
}

function comparePinnedChampionsStaticMoves(report, dex) {
  if (!report || typeof report !== "object" || Array.isArray(report)
      || report.status !== "CHAMPIONS_STATIC_24_SOURCE_BOUND"
      || report.source_fields_verified !== true
      || report.champions_engine_parity_verified !== false
      || report.ranking_status !== "NO_RANKING"
      || !HEX.test(report.team_sha256 || "")
      || !HEX.test(report.champions_move_master_sha256 || "")
      || !Array.isArray(report.slots) || report.slots.length !== 24
      || !dex || !dex.moves || typeof dex.moves.get !== "function") {
    return refused("INVALID_OR_UNBOUND_SOURCE_STATIC_24");
  }
  const pairs = new Set();
  const uniqueMembers = new Set();
  const different = [], missing = [];
  let equal = 0;
  for (const slot of report.slots) {
    if (!slot || typeof slot !== "object" || Array.isArray(slot)
        || typeof slot.registered_member !== "string"
        || !slot.registered_member || slot.registered_member.length > 120
        || !Number.isInteger(slot.slot) || slot.slot < 1 || slot.slot > 4
        || !ID.test(slot.move_id || "")
        || typeof slot.champions_name !== "string" || !slot.champions_name
        || !TYPES.has(slot.type) || !CATEGORIES.has(slot.category)
        || !Number.isInteger(slot.base_power) || slot.base_power < 0
        || slot.base_power > 300
        || !(slot.accuracy === true
             || Number.isInteger(slot.accuracy) && slot.accuracy >= 1
                && slot.accuracy <= 100)
        || !Number.isInteger(slot.pp) || slot.pp < 1 || slot.pp > 64
        || !Number.isInteger(slot.priority)
        || slot.priority < -7 || slot.priority > 7) {
      return refused("INVALID_REGISTERED_STATIC_FIELD");
    }
    const pair = slot.registered_member + ":" + slot.slot;
    if (pairs.has(pair)) return refused("DUPLICATED_MOVE_SLOT");
    pairs.add(pair);
    uniqueMembers.add(slot.registered_member);
    const move = dex.moves.get(slot.move_id);
    if (!move || !move.exists || move.id !== slot.move_id) {
      missing.push(pair);
      continue;
    }
    const mismatch = [];
    const expected = {
      type:slot.type, category:slot.category, basePower:slot.base_power,
      accuracy:slot.accuracy, pp:slot.pp, priority:slot.priority,
    };
    for (const [key, value] of Object.entries(expected)) {
      if (move[key] !== value) mismatch.push(key);
    }
    if (mismatch.length) different.push({slot:pair, fields:mismatch});
    else equal++;
  }
  if (pairs.size !== 24 || uniqueMembers.size !== 6) {
    return refused("EXPECTED_SIX_MEMBERS_FOUR_MOVES");
  }
  return Object.freeze({
    status:missing.length ? "PINNED_STATIC_IDS_MISSING"
      : different.length ? "PINNED_STATIC_FIELDS_DIFFER"
      : "PINNED_STATIC_FIELDS_EQUAL_UNATTESTED",
    reason:"PINNED_METADATA_NOT_GAME_EFFECT_OR_LEGALITY_PARITY",
    checked_slots:24, equal_slots:equal, mismatched:different,
    missing, game_mechanics_verified:false, ready_for_live:false,
    ranking_status:"NO_RANKING",
  });
}

module.exports = {comparePinnedChampionsStaticMoves};
