"use strict";
// Phase 0 ONLY: a source-method discrepancy with pinned executed turn evidence.
// This is neither a game parity check nor permission to use a guaranteed KO.
const {LEDGER,secondaryComparison}=require("./champions_damage_rounding_secondary_reference.cjs");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
function checkExecutedRoundingEvidence(r){
 const ref=secondaryComparison();
 if(r?.schema!=="phase0-champions-damage-rounding-executed-v1"||
    r.pinned_sha!==PIN||r.format!=="gen9championsbssregmc"||
    r.regulation!=="M-C"||
    r.source_evidence!=="TWO_CHAMPIONS_SECONDARY_DESCRIPTIONS_NOT_DEVICE"||
    r.scope!=="16_FIXED_RNG_SYNTHETIC_EXECUTED_TURNS_ONE_LEGAL_BLASTOISE_FIXTURE"||
    JSON.stringify(r.source_ids)!==JSON.stringify(ref.source_ids)||
    r.roll_count!==16||r.executed_turn_count!==16||
    r.discriminating_witness_count!==4||r.source_conflict_at_final_step!==true||
    r.game_observed_turns!==0||r.private_golden_read!==false||
    r.live_use_approved!==false||r.gate_A!=="UNKNOWN"||
    r.gate_B!=="UNSUPPORTED"||r.adoption!=="NO_GO"||
    JSON.stringify(r.unverified)!==JSON.stringify(LEDGER.unverified)||
    !Array.isArray(r.results)||r.results.length!==16)
    throw Error("executed rounding Gate A source/evidence contract drift");
 let discriminators=0;
 for(let i=0;i<16;i++){
   const a=r.results[i],b=ref.rows[i],w=b.discriminating;
   if(!a||a.roll_pct!==b.roll_pct||
      a.actual_hp_delta!==b.sequential||
      a.pinned_direct_damage!==b.sequential||
      a.secondary_sequential!==b.sequential||
      a.secondary_combined!==b.combined||
      a.status!==(w?"EXECUTED_MATCHES_SEQUENTIAL_DIFFERS_COMBINED":
        "EXECUTED_MATCHES_BOTH_SECONDARY_FINAL_STEPS")||
      JSON.stringify(a.observed_move_order)!==JSON.stringify(["p1","p2"])||
      JSON.stringify(a.observed_moves)!==JSON.stringify({
        p1:{id:"earthquake",name:"Earthquake",priority:0},
        p2:{id:"surf",name:"Surf",priority:0}
      })||
      !Number.isInteger(a.seed)||a.seed<1||a.seed>48||
      !Array.isArray(a.event_trace)||a.event_trace.length!==5||
      !/^\|move\|p1a: Garchomp\|Earthquake\|p2a: Blastoise$/.test(a.event_trace[0])||
      a.event_trace[1]!=="|split|p2"||
      !/^\|-damage\|p2a: Blastoise\|\d+\/\d+$/.test(a.event_trace[2])||
      !/^\|-damage\|p2a: Blastoise\|\d+\/100$/.test(a.event_trace[3])||
      !/^\|move\|p2a: Blastoise\|Surf\|p1a: Garchomp$/.test(a.event_trace[4])||
      a.provenance!=="PINNED_SYNTHETIC_EXECUTED_TURN_BURN_REFLECT_NOT_DEVICE")
      throw Error("executed rounding Gate A source/turn drift: "+b.roll_pct);
   // The visible exact HP and rounded percentage are not two distinct hits.
   const h=a.event_trace[2].match(/\|(\d+)\/(\d+)$/);
   const pct=a.event_trace[3].match(/\|(\d+)\/100$/);
   if(!h||!pct||Number(h[2])!==154||
      154-Number(h[1])!==a.actual_hp_delta||
      Math.floor(Number(h[1])*100/154)!==Number(pct[1]))
      throw Error("executed rounding Gate A split HP-view inconsistency");
   if(w)discriminators++;
 }
 if(discriminators!==4)
   throw Error("executed rounding discriminator count drift");
 return {synthetic_executed_turns:16,source_discriminators:4,
   game_verified_turns:0,comparison_scope:"TWO_SECONDARY_LOCAL_FINAL_STEPS_VS_PINNED_EXECUTED_TURNS_NOT_DEVICE"};
}
module.exports={checkExecutedRoundingEvidence};
