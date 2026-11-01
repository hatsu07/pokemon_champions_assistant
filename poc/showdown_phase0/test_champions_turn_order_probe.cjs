"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {LEDGER,validateLedger,baseSpeed,effectiveSpeed,compareOrder,referenceCase}=
  require("./champions_turn_order_reference.cjs");
const {run,observeMoves,makeFixture}=require("./champions_turn_order_probe.cjs");
test("Champions-specific source contracts bind IDs, case flags, priorities and URLs",()=>{
 assert.equal(validateLedger(),true);
 assert.equal(LEDGER.cases.length,10);
 for(const mutate of [
   x=>{x.cases[1].m_scarf=false;},
   x=>{x.cases[6].expected="p1";},
   x=>{x.cases[7].trick_room=false;},
   x=>{x.reviewed_moves[4].priority=0;},
   x=>{x.sources[0].url="https://unreviewed.example";},
   x=>{x.live_use_approved=true;},
   x=>{x.actual_game_order_verified=1;},
   x=>{x.cases.pop();}
 ]){
   const changed=structuredClone(LEDGER);mutate(changed);
   assert.throws(()=>validateLedger(changed),/contract drift/);
 }
});
test("independent Champions AP speeds and speed-multiplier floor boundaries",()=>{
 assert.equal(baseSpeed("Garchomp",0),122);
 assert.equal(baseSpeed("Garchomp",2),124);
 assert.equal(baseSpeed("Metagross",2),92);
 assert.equal(baseSpeed("Metagross",32),122);
 const state=(baseSpeed,stage=0,scarf=false,priority=0)=>({baseSpeed,stage,scarf,priority});
 assert.equal(effectiveSpeed(state(92,0,true)),138);
 assert.equal(effectiveSpeed(state(92,1,false)),138);
 assert.equal(effectiveSpeed(state(123,0,true)),184);
 assert.throws(()=>effectiveSpeed(state(124,1,true)),/UNSUPPORTED_COMBINED/);
 assert.throws(()=>effectiveSpeed(state(124,-1,false)),/UNSUPPORTED_CHAMPIONS_ORDER/);
 assert.throws(()=>effectiveSpeed({...state(124),bonus:2}),/unreviewed speed state/);
 assert.throws(()=>baseSpeed("Garchomp-Mega",2),/UNSUPPORTED/);
});
test("priority dominates Speed and Trick Room, while same-priority reversed ordering is explicit",()=>{
 const p=(speed,priority,scarf=false)=>({baseSpeed:speed,stage:0,scarf,priority});
 assert.equal(compareOrder(p(124,0),p(92,1),false).expected_first,"p2");
 assert.equal(compareOrder(p(124,4),p(92,1,true),false).expected_first,"p1");
 assert.equal(compareOrder(p(124,0),p(92,0),true).expected_first,"p2");
 assert.equal(compareOrder(p(124,4),p(92,1),true).expected_first,"p1");
 const tie=compareOrder(p(122,0),p(122,0),false);
 assert.equal(tie.expected_first,"UNRESOLVED_TIE");
 assert.equal(tie.status,"UNKNOWN_TIE");
 assert.throws(()=>compareOrder(p(124,0),p(92,0),"trickroom"),/unreviewed Trick Room/);
});
test("all source conditions have independently calculated expected orders",()=>{
 assert.deepEqual(LEDGER.cases.map(referenceCase).map(x=>x.expected_first),
   LEDGER.cases.map(x=>x.expected));
 assert.equal(makeFixture(LEDGER.cases[1]).defender.item,"Choice Scarf");
 assert.equal(makeFixture(LEDGER.cases[6]).defender.evs.spe,32);
 assert.equal(observeMoves(["|move|p2a: Metagross|Iron Defense|p2a: Metagross",
   "|move|p1a: Garchomp|Swords Dance|p1a: Garchomp"]).first,"p2");
 assert.throws(()=>observeMoves(["|move|p1a: Garchomp|Swords Dance"]),/unambiguously/);
});
test("pinned M-C registered six / selected three executes order with source-only classifications",()=>{
 const r=run();
 assert.equal(r.schema,"phase0-champions-turn-order-diagnostic-v1");
 assert.equal(r.pinned_sha,"ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.official_or_device_confirmed,false);
 assert.equal(r.game_verified_turn_count,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 assert.equal(r.registered_six_member_teams,true);
 assert.equal(r.selected_three_member_teams,true);
 assert.equal(r.checked_move_priority_count,5);
 assert.equal(r.scenarios,10);
 assert.equal(r.pinned_executed_turn_count,10);
 assert.deepEqual(r.counts,{SOURCE_SPEC_MATCH:9,SOURCE_SPEC_MISMATCH:0,INCONCLUSIVE_SPEED_TIE:1});
 for(const caseRow of r.results){
   const c=LEDGER.cases.find(x=>x.id===caseRow.id);
   assert.ok(c);
   assert.equal(caseRow.expected_first,c.expected);
   assert.ok(["p1","p2"].includes(caseRow.observed_first));
   assert.equal(caseRow.observed_sequence.length,2);
   assert.equal(caseRow.status,c.expected==="UNRESOLVED_TIE"?
     "INCONCLUSIVE_SPEED_TIE":"SOURCE_SPEC_MATCH");
 }
 const tied=r.results.find(x=>x.id==="speed-tie-unresolved");
 assert.equal(tied.p1_effective_speed,tied.p2_effective_speed);
 assert.equal(tied.comparison_scope,"TIE_OUTCOME_NOT_A_SINGLE_VALUE_ASSERTION");
 assert.ok(r.unverified.some(s=>s.includes("Mega")));
});
