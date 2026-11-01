"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {LEDGER,checkLedger,speeds,reference}=require("./champions_paralysis_reference.cjs");
const {run,NUM_SEEDS}=require("./champions_paralysis_probe.cjs");
test("Champions secondary-source rate, URL, cases and device-evidence boundary are fixed",()=>{
 assert.equal(checkLedger(),true);
 assert.equal(LEDGER.cases.length,10);
 for(const mutate of [
   l=>{l.full_paralysis_rate.denominator=4},
   l=>{l.sources[0].url="https://unreviewed.example"},
   l=>{l.sources[0].basis="Champions device verified";},
   l=>{l.sources[4].basis="Device-proven Trick Room order";},
   l=>{l.not_verified[0]="Live game turn ordering verified";},
   l=>{l.not_verified[5]="Full paralysis probability device-confirmed";},
   l=>{l.sources[2].extra="official verification";},
   l=>{l.cases[1].g_par=false},
   l=>{l.cases[3].trick_room=false},
   l=>{l.cases[5].expected="p2"},
   l=>{l.cases[9].expected="p1"},
   l=>{l.game_verified_turn_count=1},
   l=>{l.private_golden_read=true},
   l=>{l.live_use_approved=true},
   l=>{l.cases.pop()}
 ]){
   const modified=structuredClone(LEDGER);mutate(modified);
   assert.throws(()=>checkLedger(modified),/source contract drift/);
 }
});
test("independent paralysis half floors and combined invariance vs unresolved rounding",()=>{
 const s=(base,par=false,scarf=false,stage=0)=>speeds({base,par,scarf,stage});
 assert.deepEqual(s(191,true).possible_speeds,[95]);
 assert.equal(s(100,true).effective_speed,50);
 assert.equal(s(124,true).effective_speed,62);
 assert.equal(s(92,true).effective_speed,46);
 assert.equal(s(124,true,true).effective_speed,93);
 assert.equal(s(92,true,false,1).effective_speed,69);
 assert.equal(s(92,true,true).effective_speed,69);
 const unknown=s(93,true,true,1);
 assert.equal(unknown.status,"UNVERIFIED_MODIFIER_ORDER");
 assert.equal(unknown.effective_speed,null);
 assert.deepEqual(unknown.possible_speeds,[103,104]);
 assert.throws(()=>s(92,false,true,-1),/UNSUPPORTED/);
 assert.throws(()=>speeds({base:92,par:true,scarf:true,stage:0,weather:"rain"}),/UNSUPPORTED/);
});
test("priority precedes paralysis and Trick Room, while triple odd rounding stays unknown",()=>{
 const r=LEDGER.cases.map(reference);
 assert.deepEqual(r.map(x=>x.expected_first),LEDGER.cases.map(c=>c.expected));
 assert.equal(r[0].p1_base_speed,124);
 assert.equal(r[0].p2_base_speed,92);
 assert.deepEqual([r[0].p1_effective_speed,r[0].p2_effective_speed],[124,46]);
 assert.equal(r[1].p1_effective_speed,62);
 assert.equal(r[3].expected_first,"p2");
 assert.equal(r[4].p2_priority,1);
 assert.equal(r[5].p1_priority,4);
 assert.equal(r[6].p1_effective_speed,93);
 assert.equal(r[9].p2_effective_speed,null);
 assert.equal(r[9].source_status,"UNKNOWN_ROUNDING");
 assert.throws(()=>reference(structuredClone(LEDGER.cases[0])),/unreviewed paralysis case identity/);
});
test("actual pinned executed turns check status Speed without inventing full paralysis rates",()=>{
 const r=run();
 assert.equal(r.schema,"phase0-champions-paralysis-order-diagnostic-v1");
 assert.equal(r.pinned_sha,"ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
 assert.equal(r.format,"gen9championsbssregmc");
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.game_verified_turn_count,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 assert.equal(r.checked_move_priorities,5);
 assert.equal(r.pinned_implementation.pinned_full_paralysis_chance,"1/8");
 assert.equal(r.pinned_implementation.pinned_speed_half_floor,true);
 assert.equal(r.source_full_paralysis_rate,"1/8");
 assert.equal(r.full_paralysis_sample.synthetic_turns,NUM_SEEDS);
 assert.equal(r.full_paralysis_sample.interrupted+r.full_paralysis_sample.uninterrupted,NUM_SEEDS);
 assert.equal(r.full_paralysis_sample.empirical_probability_verified,false);
 assert.equal(r.case_count,10);
 assert.deepEqual(r.counts,{SOURCE_SPEC_MATCH:9,SOURCE_SPEC_MISMATCH:0,SOURCE_UNKNOWN_MODIFIER_ORDER:1});
 assert.equal(r.results.length,10);
 for(const [i,row] of r.results.entries()){
   const c=LEDGER.cases[i],ref=reference(c);
   assert.equal(row.id,c.id);
   assert.equal(row.source_expectation,ref.expected_first);
   assert.equal(row.observed_sequence.length,2);
   assert.ok(["p1","p2"].includes(row.observed_first));
   assert.ok(row.executed_seed>=1&&row.executed_seed<=NUM_SEEDS);
   assert.ok(row.full_paralysis_preempted_attempts>=0);
   assert.equal(row.executed_moves.p1.name,c.p1);
   assert.equal(row.executed_moves.p2.name,c.p2);
   assert.equal(row.executed_moves.p1.priority,row.p1_priority);
   assert.equal(row.executed_moves.p2.priority,row.p2_priority);
   if(i===9){
     assert.equal(row.status,"SOURCE_UNKNOWN_MODIFIER_ORDER");
     assert.equal(row.p2_effective_speed,null);
     assert.deepEqual(row.p2_possible_speeds,[103,104]);
   }else{
     assert.equal(row.status,"SOURCE_SPEC_MATCH");
     assert.equal(row.observed_first,row.source_expectation);
     assert.equal(row.pinned_effective_speed.p1,row.p1_effective_speed);
     assert.equal(row.pinned_effective_speed.p2,row.p2_effective_speed);
   }
 }
 assert.ok(r.unverified.some(v=>v.includes("Same-turn paralysis")));
});
