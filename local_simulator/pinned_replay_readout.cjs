"use strict";

// Read-only extraction from a COMPLETE, externally supplied synthetic pinned
// Showdown Battle. No real Champions OCR/provenance, opponent mapping, or RNG
// reconstruction. In particular this is NOT an engine authorization ticket.
const crypto=require("node:crypto");
const {isDeepStrictEqual}=require("node:util");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const SOURCE="PINNED_SHOWDOWN_COMPLETE_SYNTHETIC_REPLAY";
const BOOSTS=["atk","def","spa","spd","spe","accuracy","evasion"];

function sha(value){
  return crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}
function fail(reason) {
  return {status:"NO_ENGINE_REPLAY_READOUT",reason,
    full_state_reconstructed:false,ready_for_engine:false,
    opponent_slot_mapping_authenticated:false};
}
function plain(x){
  return x!==null && typeof x==="object" && !Array.isArray(x) &&
    Object.getPrototypeOf(x)===Object.prototype;
}
function extractPinnedReplayReadout(Battle, battleJson) {
  try {
    // READOUT_REPLAY supplies the exact canonical UTF-8 JSON text already
    // hashed by Python. A standalone synthetic caller may still pass an
    // object; BOTH paths hash precisely the text used for fromJSON.
    if(typeof Battle?.fromJSON!=="function" ||
       (typeof battleJson!=="string" && !plain(battleJson)))
      return fail("COMPLETE_SYNTHETIC_BATTLE_REQUIRED");
    const original=typeof battleJson==="string" ?
      battleJson : JSON.stringify(battleJson);
    if(Buffer.byteLength(original,"utf8")>4*1024*1024)
      return fail("REPLAY_SIZE_LIMIT");
    const parsed=JSON.parse(original);
    if(!plain(parsed))return fail("COMPLETE_SYNTHETIC_BATTLE_REQUIRED");
    // Clone the entire caller input *before* asking the real engine to
    // rehydrate. Do not use a mutable raw Battle or populate from OCR.
    const b=Battle.fromJSON(parsed);
    if(b.format?.mod!=="champions" || b.gameType!=="singles" ||
       b.activePerHalf!==1 || !["move","switch"].includes(b.requestState) ||
       !Array.isArray(b.sides) || b.sides.length!==2 ||
       !b.sides.every((s,i)=>s?.id==="p"+(i+1) &&
          Array.isArray(s.pokemon) && s.pokemon.length===3 &&
          Array.isArray(s.active) && s.active.length===1))
      return fail("UNSUPPORTED_ENGINE_BATTLE_SCOPE");
    // No silent projection of a partial/fromJSON-modified representation.
    if(!isDeepStrictEqual(b.toJSON(),parsed))
      return fail("ENGINE_REPLAY_ROUNDTRIP_MISMATCH");
    const active={};
    const members=[];
    for(let i=0;i<2;i++){
      const side=b.sides[i],label="p"+(i+1);
      const slot=side.pokemon.indexOf(side.active[0])+1;
      if(slot<1||slot>3) return fail("ACTIVE_NOT_IN_ENGINE_PARTY");
      active[label]=slot;
      for(let j=0;j<3;j++){
        const p=side.pokemon[j];
        const moveIds=p?.moveSlots?.map(m=>m?.id);
        if(typeof p?.species?.id!=="string" ||
           !/^[a-z0-9]+$/.test(p.species.id) ||
           !Array.isArray(moveIds) || !moveIds.length || moveIds.length>4 ||
           moveIds.some(m=>typeof m!=="string" || !/^[a-z0-9]+$/.test(m)) ||
           new Set(moveIds).size!==moveIds.length ||
           !Number.isSafeInteger(p.hp) || !Number.isSafeInteger(p.maxhp) ||
           p.maxhp<1 || p.hp<0 || p.hp>p.maxhp ||
           typeof p.fainted!=="boolean" || p.fainted!==(p.hp===0) ||
           !plain(p.boosts))return fail("UNSUPPORTED_ENGINE_MEMBER");
        const boosts=[];
        for(const key of BOOSTS){
          const v=p.boosts[key];
          if(!Number.isInteger(v)||v< -6||v>6)
            return fail("UNSUPPORTED_ENGINE_BOOST");
          // All seven explicit engine stages are included, even 0. They
          // are read-out *hypothetical* values, NOT observed facts.
          boosts.push([key,v]);
        }
        members.push({side:label,slot:j+1,species_id:p.species.id,
          move_ids:moveIds,hp:p.hp,maxhp:p.maxhp,fainted:p.fainted,boosts});
      }
    }
    if(typeof battleJson==="string" ?
       battleJson!==original : JSON.stringify(battleJson)!==original)
      return fail("REPLAY_INPUT_MUTATED");
    return {status:"SYNTHETIC_ENGINE_REPLAY_READOUT",
      source:SOURCE,engine_sha:PIN,battle_sha256:
        crypto.createHash("sha256").update(original,"utf8").digest("hex"),
      active,members,
      full_state_reconstructed:false,ready_for_engine:false,
      opponent_slot_mapping_authenticated:false,
      unverified:["REAL_CHAMPIONS_BATTLE_ORIGIN_NOT_ATTESTED",
        "OPPONENT_DISPLAY_TO_ENGINE_SLOT_NOT_ATTESTED",
        "UNOBSERVED_RNG_AND_EVENT_HISTORY_NOT_ATTESTED",
        "CHAMPIONS_SHOWDOWN_PARITY_NOT_VERIFIED"]};
  }catch(_err){return fail("ENGINE_REPLAY_READOUT_UNAVAILABLE");}
}
module.exports={extractPinnedReplayReadout,SOURCE,PIN,sha};
