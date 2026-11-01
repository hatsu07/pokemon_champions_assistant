"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {run:paralysis}=require("./champions_paralysis_probe.cjs");
const {checkParalysisEvidence}=require("./champions_paralysis_gate_a_contract.cjs");
const {run:gateA,summarize,MAP}=require("./gate_a_evidence_inventory.cjs");
test("Gate A checks 9 reviewed paralysis turns, 1 source unknown, never a game proof",()=>{
 const p=paralysis();
 assert.deepEqual(checkParalysisEvidence(p),
   {matches:9,rounding_unknown:1,scope:"SYNTHETIC_PINNED_TURNS_NOT_DEVICE"});
 const a=gateA();
 assert.equal(a.paralysis_source_order_matches,9);
 assert.equal(a.paralysis_unknown_compound_rounding,1);
 assert.equal(a.paralysis_pinned_rate,"1/8_SOURCE_ONLY_NOT_DEVICE");
 assert.equal(a.game_verified_checks_this_run,0);
 assert.equal(a.safe_to_integrate_with_live,false);
 assert.deepEqual([a.gate_A,a.gate_B,a.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 const {compare}=require("./audit.cjs");
 const {main:wiki}=require("./wiki_damage_compare.cjs");
 const {main:events}=require("./champions_event_probe.cjs");
 const {main:modifier}=require("./champions_damage_modifier_probe.cjs");
 const {run:order}=require("./champions_turn_order_probe.cjs");
 const x=compare(),w=wiki(),e=events(),m=modifier(),t=order();
 assert.throws(()=>summarize(x,w,e,MAP,m,t,null),/paralysis Gate A source contract/);
 for(const mutate of [
   o=>{o.game_verified_turn_count=1;},
   o=>{o.live_use_approved=true;},
   o=>{o.gate_A="CONFIRMED";},
   o=>{o.pinned_implementation.pinned_full_paralysis_chance="1/4";},
   o=>{o.pinned_implementation.evidence="GAME_VERIFIED";},
   o=>{o.unverified[0]="Live game turn ordering verified";},
   o=>{o.unverified[5]="Full paralysis distribution device proven";},
   o=>{o.full_paralysis_sample.empirical_probability_verified=true;},
   o=>{o.full_paralysis_sample.scope="GAME_VERIFIED";},
   o=>{o.full_paralysis_sample.synthetic_turns=10;},
   o=>{o.full_paralysis_sample.interrupted=0; o.full_paralysis_sample.uninterrupted=48;},
   o=>{o.full_paralysis_sample.witness.event="|cant|p2a: Metagross|slp";},
   o=>{o.full_paralysis_sample.witness.status_before="";},
   o=>{o.full_paralysis_sample.witness.side="p1";},
   o=>{o.full_paralysis_sample.witness.seed=99;},
   o=>{o.full_paralysis_sample.interruption_path_observed=false;},
   o=>{o.full_paralysis_sample.sample_case_id="garchomp-par";},
   o=>{o.counts.SOURCE_SPEC_MATCH=10;},
   o=>{o.results[9].status="SOURCE_SPEC_MATCH";},
   o=>{o.results[9].p2_effective_speed=104;},
   o=>{o.results[0].executed_moves.p2.name="Bullet Punch";},
   o=>{o.results[0].executed_moves.p1.id="protect";},
   o=>{o.results[0].p2_priority=1;},
   o=>{o.results[0].observed_first="p2";},
   o=>{o.results[1].p1_effective_speed++;},
   o=>{o.results[1].executed_seed=49;},
   o=>{o.results.pop();}
 ]){
   const r=structuredClone(p);mutate(r);
   assert.throws(()=>checkParalysisEvidence(r),/paralysis Gate A/);
   assert.throws(()=>summarize(x,w,e,MAP,m,t,r),/paralysis Gate A/);
 }
});
