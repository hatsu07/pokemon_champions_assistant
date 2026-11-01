"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {main,referenceFor,compareRolls,validateSource,oddRoundingWitnesses,
  REVIEWED_IDS,WITNESS_ID,LEDGER}=
  require("./champions_damage_modifier_probe.cjs");
const {calculate,halfDownRatio}=require("./wiki_damage_reference.cjs");
test("nine named Champions secondary-source modifier cases are reviewed",()=>{
 assert.equal(validateSource(),true);
 assert.deepEqual(LEDGER.scenarios.map(c=>c.id),REVIEWED_IDS);
 assert.equal(LEDGER.private_golden_read,false);
 for(const c of LEDGER.scenarios){
   const r=referenceFor(c);
   assert.equal(r.rolls.length,16);
   assert.equal(r.status,"WIKI_ARITHMETIC_ONLY");
 }
});
test("critical bypasses Reflect but burn remains: independently sourced arithmetic",()=>{
 const lookup=id=>referenceFor(LEDGER.scenarios.find(x=>x.id===id)).rolls;
 assert.deepEqual(lookup("critical"),lookup("critical-ignores-reflect"));
 assert.deepEqual(lookup("critical-preserves-burn"),
   lookup("critical-ignores-reflect-preserves-burn"));
 assert.deepEqual(lookup("reflect"),lookup("burn"));
 assert.notDeepEqual(lookup("burn-and-reflect"),lookup("burn"));
 assert.notDeepEqual(lookup("critical-preserves-burn"),lookup("critical"));
});
test("P1: odd pre-final damage distinguishes combined vs separately rounded modifiers",()=>{
 const c=LEDGER.scenarios.find(x=>x.id===WITNESS_ID);
 const witnesses=oddRoundingWitnesses(c);
 assert.ok(witnesses.length>0);
 assert.equal(halfDownRatio(67n,[1n,4n]),17n);
 assert.equal(halfDownRatio(halfDownRatio(67n,[1n,2n]),[1n,2n]),16n);
 for(const w of witnesses){
   assert.equal(w.pre_final_modifier%2,1);
   assert.notEqual(w.combined_rounding,w.split_rounding);
   assert.equal(w.combined_rounding,w.reference_damage);
 }
});
test("P2: one flipped condition flag or source URL fails closed",()=>{
 const changes=[
   ["critical-ignores-reflect","reflect",false],
   ["critical-ignores-reflect-preserves-burn","reflect",false],
   ["burn-and-reflect","burn",false],
   ["critical-preserves-burn","critical",false],
   [WITNESS_ID,"reflect",false]
 ];
 for(const [id,key,value] of changes){
   const corrupted=structuredClone(LEDGER);
   corrupted.scenarios.find(s=>s.id===id)[key]=value;
   assert.throws(()=>validateSource(corrupted),/unreviewed damage modifier contract/);
 }
 const altered=structuredClone(LEDGER);
 altered.sources[0].url="https://example.com/substituted";
 assert.throws(()=>validateSource(altered),/unreviewed damage source/);
 const moved=structuredClone(LEDGER);
 moved.scenarios.find(c=>c.id===WITNESS_ID).fixture="UNKNOWN";
 assert.throws(()=>validateSource(moved),/unreviewed damage modifier contract/);
});
test("tampered source fixture and malformed results fail closed",()=>{
 assert.throws(()=>compareRolls([1,2],[1,2]),/unreadable 16/);
 const x=Array(16).fill(9),y=[...x];y[3]++;
 const r=compareRolls(x,y);
 assert.equal(r.status,"WIKI_SPEC_MISMATCH");
 assert.deepEqual(r.differences,[{roll_pct:88,reference:9,pinned_showdown:10}]);
 assert.throws(()=>compareRolls(x,[...x.slice(1),null]),/unreadable 16/);
});
test("nine legal registered-six selected-three damage conditions include real odd rounding witnesses",()=>{
 const r=main();
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.scenario_count,9);
 assert.equal(r.individual_roll_comparisons,144);
 assert.ok(r.odd_rounding_witness_count>0);
 assert.equal(r.odd_rounding_witness_verified_in_pinned_getDamage,true);
 assert.equal(r.counts.WIKI_SPEC_MISMATCH,0);
 assert.equal(r.counts.WIKI_SPEC_MATCH,9);
 assert.equal(r.executed_turns,0);
 assert.equal(r.game_verified_check_count,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 for(const c of r.results){
   assert.equal(c.status,"WIKI_SPEC_MATCH");
   assert.equal(c.difference_count,0);
 }
 const odd=r.results.find(c=>c.id===WITNESS_ID);
 assert.equal(odd.defender,"Blastoise");
 assert.ok(odd.odd_rounding_witnesses.length);
 for(const witness of odd.odd_rounding_witnesses){
   assert.notEqual(witness.combined_rounding,witness.split_rounding);
 }
});
