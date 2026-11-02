"use strict";

// Read-only Phase 2 experiment: compare an explicitly normalized *synthetic*
// visible-state packet against an independently restored complete pinned
// Showdown Battle. This DOES NOT reconstruct a Battle from an actual screen,
// attest to an observation's provenance, verify hidden sets/RNG or enable LIVE.
const crypto = require("node:crypto");

const SOURCE = "SYNTHETIC_PINNED_SHOWDOWN_PROJECTION";
const BOOSTS = new Set(["atk","def","spa","spd","spe","accuracy","evasion"]);
const ROOT = new Set([
  "source","match_id","decision_window_id","state_revision",
  "battle_sha256","active","members","field","turn"
]);
const MEMBER = new Set([
  "side","slot","species_id","hp","maxhp","fainted","status","boosts"
]);
const UNVERIFIED = Object.freeze([
  "ORIGIN_OF_REAL_GAME_EVIDENCE_NOT_ATTESTED",
  "OPPONENT_HIDDEN_SETS_NOT_OBSERVED",
  "BATTLE_RNG_AND_EVENT_QUEUE_NOT_OBSERVED",
  "EFFECT_DURATIONS_AND_UNOBSERVED_SIDE_STATE_NOT_VERIFIED",
  "CHAMPIONS_REAL_GAME_PARITY_NOT_VERIFIED",
]);

function plain(value) {
  return value !== null && typeof value === "object" &&
    !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}
function hasKeys(value, keys) {
  return plain(value) && Object.keys(value).every(k=>keys.has(k));
}
function sha256(value) {
  return crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}
function failure(reason, field="") {
  return {status:"NO_OBSERVATION_MATCH",reason,offending_field:field,
    checked_fields:[],full_state_reconstructed:false,
    exhaustive_observation_verified:false,unverified:UNVERIFIED.slice()};
}

function auditSyntheticObservedReplay(Battle, battleJson, packet) {
  try {
    if (!Battle || typeof Battle.fromJSON!=="function" ||
        !plain(battleJson) || !hasKeys(packet,ROOT))
      return failure("INVALID_REPLAY_OR_PROJECTION");
    // An arbitrary OCR or asserted real-game packet is NOT sufficient
    // provenance. This scope is only for controlled synthetic comparisons.
    if (packet.source!==SOURCE)
      return failure("REAL_GAME_OBSERVATION_NOT_SUPPORTED","source");
    if (typeof packet.match_id!=="string" || !packet.match_id.trim() ||
        packet.match_id!==packet.match_id.trim() ||
        typeof packet.decision_window_id!=="string" ||
        !packet.decision_window_id.trim() ||
        packet.decision_window_id!==packet.decision_window_id.trim() ||
        !Number.isSafeInteger(packet.state_revision) || packet.state_revision<0)
      return failure("INVALID_SYNTHETIC_IDENTITY");
    const original=JSON.stringify(battleJson), digest=sha256(battleJson);
    if (packet.battle_sha256!==digest)
      return failure("REPLAY_SHA_MISMATCH","battle_sha256");
    if (!hasKeys(packet.active,new Set(["p1","p2"])) ||
        Object.keys(packet.active).length!==2 ||
        ![packet.active.p1,packet.active.p2].every(
          i=>Number.isInteger(i)&&i>=1&&i<=3))
      return failure("INVALID_OBSERVED_ACTIVE");
    if (!Array.isArray(packet.members) || !packet.members.length ||
        packet.members.length>6)
      return failure("INVALID_OBSERVED_MEMBERS");
    if (packet.turn!==undefined &&
        (!Number.isSafeInteger(packet.turn) || packet.turn<1))
      return failure("INVALID_OBSERVED_TURN","turn");
    if (packet.field!==undefined &&
        (!hasKeys(packet.field,new Set(["weather","terrain"])) ||
         Object.keys(packet.field).length===0 ||
         Object.values(packet.field).some(v=>typeof v!=="string" ||
           !/^[a-z0-9]*$/.test(v))))
      return failure("INVALID_OBSERVED_FIELD","field");

    const seen=new Set();
    for (const [i,m] of packet.members.entries()) {
      if (!hasKeys(m,MEMBER) || !["p1","p2"].includes(m.side) ||
          !Number.isInteger(m.slot) || m.slot<1 || m.slot>3 ||
          Object.keys(m).length<=2)
        return failure("INVALID_OBSERVED_MEMBER","members["+i+"]");
      const key=m.side+":"+m.slot;
      if (seen.has(key)) return failure("DUPLICATE_OBSERVED_MEMBER",key);
      seen.add(key);
      if (m.species_id!==undefined &&
          (typeof m.species_id!=="string" ||
           !/^[a-z0-9]+$/.test(m.species_id)))
        return failure("INVALID_OBSERVED_SPECIES",key);
      for (const part of ["hp","maxhp"]) {
        if (m[part]!==undefined &&
            (!Number.isSafeInteger(m[part]) || m[part]<0 ||
             (part==="maxhp" && m[part]<1)))
          return failure("INVALID_OBSERVED_HP",key+"."+part);
      }
      if (m.hp!==undefined && m.maxhp!==undefined && m.hp>m.maxhp)
        return failure("INVALID_OBSERVED_HP",key+".hp");
      if (m.fainted!==undefined && typeof m.fainted!=="boolean")
        return failure("INVALID_OBSERVED_FAINTED",key);
      if (m.hp!==undefined && m.fainted!==undefined &&
          m.fainted!==(m.hp===0))
        return failure("CONTRADICTORY_OBSERVED_HP",key);
      if (m.status!==undefined &&
          (typeof m.status!=="string" ||
           !/^[a-z]{0,12}$/.test(m.status)))
        return failure("INVALID_OBSERVED_STATUS",key);
      if (m.boosts!==undefined &&
          (!plain(m.boosts) || !Object.keys(m.boosts).length ||
           Object.entries(m.boosts).some(([k,v])=>!BOOSTS.has(k) ||
             !Number.isInteger(v) || v < -6 || v > 6)))
        return failure("INVALID_OBSERVED_BOOSTS",key);
    }

    // Rehydrate a detached full Battle; NEVER fill missing values from the
    // observed packet, never mutate the supplied input or infer a hidden move.
    const b=Battle.fromJSON(JSON.parse(original));
    if (b.format?.mod!=="champions" || b.gameType!=="singles" ||
        b.activePerHalf!==1 || !["move","switch"].includes(b.requestState) ||
        !Array.isArray(b.sides) || b.sides.length!==2 ||
        !b.sides.every((s,i)=>s?.id==="p"+(i+1) &&
          Array.isArray(s.pokemon) && s.pokemon.length===3 &&
          Array.isArray(s.active) && s.active.length===1))
      return failure("UNSUPPORTED_ENGINE_STATE");
    const checked=[];
    for (let si=0;si<2;si++) {
      const id="p"+(si+1), side=b.sides[si];
      if (side.active[0]!==side.pokemon[packet.active[id]-1])
        return failure("VISIBLE_STATE_MISMATCH","active."+id);
      checked.push("active."+id);
    }
    if (packet.turn!==undefined) {
      if (b.turn!==packet.turn)
        return failure("VISIBLE_STATE_MISMATCH","turn");
      checked.push("turn");
    }
    if (packet.field!==undefined) {
      for (const [k,v] of Object.entries(packet.field)) {
        if (!b.field || b.field[k]!==v)
          return failure("VISIBLE_STATE_MISMATCH","field."+k);
        checked.push("field."+k);
      }
    }
    for (const m of packet.members) {
      const key=m.side+":"+m.slot;
      const p=b.sides[m.side==="p1"?0:1].pokemon[m.slot-1];
      if (!p || !Number.isSafeInteger(p.hp) || !Number.isSafeInteger(p.maxhp))
        return failure("UNSUPPORTED_ENGINE_MEMBER",key);
      for(const [part,actual] of [
        ["species_id",p.species?.id],["hp",p.hp],["maxhp",p.maxhp],
        ["fainted",p.fainted],["status",p.status]
      ]) {
        if (m[part]===undefined)continue; // absent means UNKNOWN, never zero
        if (m[part]!==actual)
          return failure("VISIBLE_STATE_MISMATCH",key+"."+part);
        checked.push(key+"."+part);
      }
      if (m.boosts!==undefined) {
        for (const [k,v] of Object.entries(m.boosts)) {
          if (p.boosts?.[k]!==v)
            return failure("VISIBLE_STATE_MISMATCH",key+".boosts."+k);
          checked.push(key+".boosts."+k);
        }
      }
    }
    if (JSON.stringify(battleJson)!==original)
      return failure("REPLAY_INPUT_MUTATED");
    return {
      status:"SYNTHETIC_VISIBLE_FIELDS_MATCH",
      reason:"MATCHED_ONLY_EXPLICITLY_SUPPLIED_FIELDS",
      battle_sha256:digest,match_id:packet.match_id,
      decision_window_id:packet.decision_window_id,
      state_revision:packet.state_revision,checked_fields:checked,
      full_state_reconstructed:false,
      exhaustive_observation_verified:false,
      unverified:UNVERIFIED.slice()
    };
  } catch (_err) {
    return failure("REPLAY_PROJECTION_UNAVAILABLE");
  }
}

module.exports={auditSyntheticObservedReplay,SOURCE,sha256};
