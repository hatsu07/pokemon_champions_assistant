"use strict";
/**
 * #141②: pinned Showdown metadata existence for the ACTUAL registered
 * Champions roster's *unapproved* MoveKey/LearnsetKey source candidates.
 *
 * This must not be imported by the LIVE game, runtime worker or GUI. It
 * verifies only whether the same source candidate IDs resolve to entries
 * in the pinned Champions engine Dex; it cannot prove effects, movesets,
 * Mega-Z mechanics, opponent state, RNG, legality or tactical accuracy.
 */
const crypto = require("node:crypto");
const fs = require("node:fs");

const PINNED_UPSTREAM_SHA = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const ID = /^[a-z0-9]+$/;
const HEX = /^[a-f0-9]{64}$/;
const SOURCES = Object.freeze({
  team_csv: "masters/Pokemon_Champions_MC_team_2026-10-03.csv",
  champions_move_master: "masters/Pokemon_Champions_MC_move_master.csv",
  champions_linked_learnsets:
    "masters/Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
  champions_usable_pokemon: "masters/Pokemon_Champions_MC_usable_pokemon.csv",
});

function sha256(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function refusal(reason) {
  return Object.freeze({
    status: "NO_PINNED_ENGINE_ID_LOOKUP", reason,
    pinned_upstream_sha: PINNED_UPSTREAM_SHA,
    species_found: 0, moves_found: 0,
    species_missing: [], moves_missing: [],
    unsupported_forms: [], source_hashes_verified: false,
    mapping_independently_attested: false,
    champions_mechanics_verified: false,
    executable_in_live: false, ranking_status: "NO_RANKING",
  });
}

/** Fail closed on truncated/altered source bytes *before* consulting Dex. */
function auditPinnedChampionsRosterKeys(report, repoRoot, dex) {
  if (!report || typeof report !== "object" || Array.isArray(report)
      || !repoRoot || typeof repoRoot !== "string"
      || !dex || !dex.species || !dex.moves
      || typeof dex.species.get !== "function"
      || typeof dex.moves.get !== "function") {
    return refusal("INVALID_REPORT_OR_ENGINE");
  }
  if (report.status !== "CHAMPIONS_KEY_CANDIDATES_ONLY"
      || report.showdown_mapping_verified !== false
      || report.champions_mechanics_verified !== false
      || report.usable_in_live !== false
      || !Array.isArray(report.source_sha256)
      || report.source_sha256.length !== 4
      || !Array.isArray(report.members)
      || report.members.length !== 6
      || report.candidate_species_keys !== 6
      || report.candidate_move_keys !== 24
      || report.expected_move_slots !== 24
      || !Array.isArray(report.unmapped_slots)
      || report.unmapped_slots.length !== 0) {
    return refusal("UNAPPROVED_OR_INCOMPLETE_CHAMPIONS_CANDIDATES");
  }
  const sourceEntries = new Map();
  for (const item of report.source_sha256) {
    if (!Array.isArray(item) || item.length !== 2
        || !Object.hasOwn(SOURCES, item[0])
        || typeof item[1] !== "string" || !HEX.test(item[1])
        || sourceEntries.has(item[0])) {
      return refusal("MALFORMED_OR_DUPLICATED_SOURCE_MANIFEST");
    }
    sourceEntries.set(item[0], item[1]);
  }
  // The expected pathnames are a fixed, reviewed CI/source manifest.
  // Never accept caller-provided arbitrary paths or OCR content.
  try {
    for (const [kind, relative] of Object.entries(SOURCES)) {
      const expected = sourceEntries.get(kind);
      if (!expected) return refusal("MISSING_SOURCE_MANIFEST_ENTRY");
      const content = fs.readFileSync(require("node:path").join(repoRoot, relative));
      if (content.byteLength === 0 || sha256(content) !== expected) {
        return refusal("SOURCE_MASTER_SHA_MISMATCH");
      }
    }
    if (report.team_sha256 !== sourceEntries.get("team_csv")) {
      return refusal("FROZEN_TEAM_SHA_MISMATCH");
    }
  } catch {
    return refusal("SOURCE_MASTER_NOT_READABLE");
  }

  const candidateMembers = new Set();
  const missingSpecies = [];
  const missingMoves = [];
  const forms = [];
  let moveSlots = 0, seenSpecies = 0, seenMoves = 0;
  // Validate the WHOLE proposed source mapping before checking any ID.
  // The exact registered four slots per member must be populated here;
  // elsewhere partial teams may be valid but cannot pass THIS 6x4 audit.
  for (const member of report.members) {
    if (!member || typeof member !== "object"
        || typeof member.registered_name !== "string"
        || candidateMembers.has(member.registered_name)
        || !ID.test(member.proposed_base_key)
        || !Array.isArray(member.moves) || member.moves.length !== 4
        || member.species_source_status !== "IN_CHAMPIONS_USABLE_AND_LEARNSETS") {
      return refusal("INCOMPLETE_OR_AMBIGUOUS_REGISTERED_SIX");
    }
    candidateMembers.add(member.registered_name);
    if (member.form_target) {
      if (member.form_status !== "FORM_NOT_ENGINE_ATTESTED"
          && member.form_status !== "FORM_NOT_CONSISTENT_WITH_CHAMPIONS_MASTERS") {
        return refusal("INCONSISTENT_FORM_CLAIM");
      }
      forms.push(member.registered_name);
    }
    const slotSet = new Set();
    for (const move of member.moves) {
      if (!move || typeof move !== "object"
          || !Number.isInteger(move.slot)
          || move.slot < 1 || move.slot > 4
          || slotSet.has(move.slot)
          || !ID.test(move.key_candidate)
          || move.learnset_membership !== "IN_CHAMPIONS_LEARNSET"
          || !["EXACT_CHAMPIONS_NAME", "DIGIT_WIDTH_EQUIVALENT"].includes(
            move.match_type)) {
        return refusal("UNVERIFIED_OR_DUPLICATED_MOVE_SLOT");
      }
      slotSet.add(move.slot);
      moveSlots++;
    }
    if (slotSet.size !== 4) return refusal("INCOMPLETE_MOVE_SLOTS");
  }
  if (candidateMembers.size !== 6 || moveSlots !== 24) {
    return refusal("WRONG_SIX_BY_FOUR_COUNTS");
  }
  for (const member of report.members) {
    const record = dex.species.get(member.proposed_base_key);
    if (record && record.exists && record.id === member.proposed_base_key) {
      seenSpecies++;
    } else {
      missingSpecies.push(member.registered_name);
    }
    for (const move of member.moves) {
      const data = dex.moves.get(move.key_candidate);
      if (data && data.exists && data.id === move.key_candidate) {
        seenMoves++;
      } else {
        missingMoves.push(member.registered_name + ".move" + move.slot);
      }
    }
  }
  return Object.freeze({
    status: missingSpecies.length || missingMoves.length
      ? "PINNED_ENGINE_KEY_COVERAGE_INCOMPLETE"
      : "PINNED_ENGINE_KEYS_EXIST_UNATTESTED",
    reason: "DEX_ENTRY_EXISTENCE_ONLY_NOT_CHAMPIONS_COMPATIBILITY",
    pinned_upstream_sha: PINNED_UPSTREAM_SHA,
    species_found: seenSpecies, moves_found: seenMoves,
    species_missing: missingSpecies, moves_missing: missingMoves,
    unsupported_forms: forms,
    source_hashes_verified: true,
    mapping_independently_attested: false,
    champions_mechanics_verified: false,
    executable_in_live: false, ranking_status: "NO_RANKING",
  });
}

module.exports = {
  auditPinnedChampionsRosterKeys, PINNED_UPSTREAM_SHA, SOURCES,
};
