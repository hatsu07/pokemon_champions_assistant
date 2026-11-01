"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {run}=require("./champions_damage_rounding_executed_probe.cjs");
const {checkExecutedRoundingEvidence}=require("./champions_damage_rounding_gate_a_contract.cjs");
test("Gate A accepts fixed executable evidence yet rejects real-game/KO evidence promotion",()=>{
 const base=run();
 assert.deepEqual(checkExecutedRoundingEvidence(base),{
   synthetic_executed_turns:16,source_discriminators:4,game_verified_turns:0,
   comparison_scope:"TWO_SECONDARY_LOCAL_FINAL_STEPS_VS_PINNED_EXECUTED_TURNS_NOT_DEVICE"
 });
 for(const mutate of [
   x=>{x.gate_A="CONFIRMED";},
   x=>{x.gate_B="SUPPORTED";},
   x=>{x.adoption="GO";},
   x=>{x.game_observed_turns=16;},
   x=>{x.private_golden_read=true;},
   x=>{x.live_use_approved=true;},
   x=>{x.source_evidence="OFFICIAL_DEVICE";},
   x=>{x.source_conflict_at_final_step=false;},
   x=>{x.discriminating_witness_count=0;},
   x=>{x.results[1].secondary_combined=21;},
   x=>{x.results[1].actual_hp_delta=22;},
   x=>{x.results[1].pinned_direct_damage=22;},
   x=>{x.results[1].status="GAME_PARITY_CONFIRMED";},
   x=>{x.results[1].event_trace[1]="|split|p1";},
   x=>{x.results[1].event_trace[2]="|-damage|p2a: Blastoise|0/154";},
   x=>{x.results[1].event_trace[3]="|-damage|p2a: Blastoise|100/100";},
   x=>{x.results[1].event_trace.pop();},
   x=>{x.results[1].observed_moves.p1.id="protect";},
   x=>{x.results[1].seed=0;},
   x=>{x.unverified[0]="Device-verified";},
   x=>{x.results.pop();}
 ]){
   const corrupted=structuredClone(base);mutate(corrupted);
   assert.throws(()=>checkExecutedRoundingEvidence(corrupted),/Gate A/);
 }
});
