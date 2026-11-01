"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {main,referenceFor,compareRolls,validateSource,REVIEWED_IDS,LEDGER}=
  require("./champions_damage_modifier_probe.cjs");
const {calculate,halfDownRatio}=require("./wiki_damage_reference.cjs");
test("eight named Champions secondary-source modifier cases are reviewed",()=>{
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
test("burn and Reflect are combined before rounding once",()=>{
 const neutral=LEDGER.scenarios.find(x=>x.id==="burn-and-reflect");
 const reference=referenceFor(neutral);
 assert.equal(neutral.other.join("/"),"1/4");
 // Half-down is intentionally not standard half-up at a 0.5 boundary.
 assert.equal(halfDownRatio(65n,[1n,2n]),32n);
 assert.equal(halfDownRatio(65n,[1n,4n]),16n);
 assert.ok(reference.minimum>0);
});
test("tampered source fixture and malformed results fail closed",()=>{
 assert.throws(()=>compareRolls([1,2],[1,2]),/unreadable 16/);
 const x=Array(16).fill(9),y=[...x];y[3]++;
 const r=compareRolls(x,y);
 assert.equal(r.status,"WIKI_SPEC_MISMATCH");
 assert.deepEqual(r.differences,[{roll_pct:88,reference:9,pinned_showdown:10}]);
 assert.throws(()=>compareRolls(x,[...x.slice(1),null]),/unreadable 16/);
});
test("eight legal registered six-member teams (each side) match fixed simulator reference",()=>{
 const r=main();
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.scenario_count,8);
 assert.equal(r.individual_roll_comparisons,128);
 assert.equal(r.counts.WIKI_SPEC_MISMATCH,0);
 assert.equal(r.counts.WIKI_SPEC_MATCH,8);
 assert.equal(r.executed_turns,0);
 assert.equal(r.game_verified_check_count,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 for(const c of r.results){
   assert.equal(c.status,"WIKI_SPEC_MATCH");
   assert.equal(c.difference_count,0);
 }
});
