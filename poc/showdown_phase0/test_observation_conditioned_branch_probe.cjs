"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {condition,BOOSTS}=require("./observation_conditioned_branch_probe.cjs");
const {context}=require("./six_team_selection_probe.cjs");
const {projectObservation,syntheticTruth}=require("./partial_observation_probe.cjs");
const {fingerprint}=require("./state_compare.cjs");
let ctx,base;
function setup(){
 if(!ctx){
  ctx=context();
  base=projectObservation(syntheticTruth(ctx,{id:"CONDITION",truth:"A"}),
    ctx,{id:"CONDITION",truth:"A"});
 }
 return structuredClone(base);
}
function known(value,id="fixture-CONDITION"){
 return {source:"SYNTHETIC",value,evidence_ids:[id]};
}
function withHPAndBoosts({hp=100,atk=1}={}){
 const obs=setup();
 obs.facts.opponent_hp_exact=known(hp);
 obs.facts.opponent_boosts=known(Object.fromEntries(BOOSTS.map(k=>[k,k==="atk"?atk:0])));
 return obs;
}
test("known midbattle HP and all seven boosts condition four separate legal hypothetical states",()=>{
 const obs=withHPAndBoosts();
 const sourceDigest=fingerprint(obs);
 const result=condition(obs,ctx);
 assert.equal(fingerprint(obs),sourceDigest,"conditioning must never modify observed facts");
 assert.equal(result.schema,"phase0-observation-conditioned-branches-v1");
 assert.deepEqual([result.gate_A,result.gate_B,result.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(result.status,"PROVISIONAL_CONDITIONAL_SYNTHETIC");
 assert.equal(result.candidate_count,4);
 assert.deepEqual(result.candidates.map(x=>x.id),["A","B","C","D"]);
 assert.equal(new Set(result.candidates.map(x=>x.conditioned_sha256)).size,4);
 assert.equal(result.candidates.every(x=>x.conditioned_hp===100&&
   x.conditioned_boosts.atk===1),true);
 assert.equal(result.candidates.every(x=>x.base_sha256!==x.conditioned_sha256),true);
 assert.equal(result.candidates.every(x=>x.hidden_set_origin===
   "FIXED_LEGAL_HYPOTHESIS_NOT_GAME_OBSERVED"),true);
 assert.equal(result.candidates.every(x=>x.conditioned_screen_ids.length===0),true);
 assert.equal(result.game_compatibility_verified,false);
 assert.equal(result.real_game_partial_state_restored,false);
 assert.equal(result.live_recommendation_permitted,false);
 assert.equal(result.not_a_unique_true_state,true);
 assert.ok(result.unrecovered_hidden_fields.includes("internal_rng"));
 assert.ok(result.unrecovered_hidden_fields.includes("opponent_mega_history"));
 assert.ok(result.unrecovered_hidden_fields.includes("unresolved_queue"));
 const repeated=condition(obs,ctx);
 assert.deepEqual(result.candidates.map(x=>x.conditioned_sha256),
   repeated.candidates.map(x=>x.conditioned_sha256),
   "same synthetic hypothesis states must be deterministic");
 assert.deepEqual(result.candidates.map(x=>x.continuation_sha256),
   repeated.candidates.map(x=>x.continuation_sha256),
   "seeded branches should replay exactly");
});
test("observed explicit remaining Reflect duration yields provisional conditioned independent branches",()=>{
 const obs=withHPAndBoosts({hp:70,atk:2});
 obs.facts.screens=known(["reflect"]);
 obs.facts.screen_remaining_turns=known({reflect:3});
 const r=condition(obs,ctx);
 assert.equal(r.status,"PROVISIONAL_CONDITIONAL_SYNTHETIC");
 assert.equal(r.candidate_count,4);
 assert.equal(r.candidates.every(x=>x.conditioned_screen_ids.includes("reflect")),true);
 assert.ok(r.admitted_evidence_fields.includes("screen_remaining_turns"));
});
test("unknown current HP or incomplete boosts cannot silently use initialized full-HP states",()=>{
 const unknown=setup();
 unknown.facts.opponent_boosts=known(Object.fromEntries(BOOSTS.map(k=>[k,0])));
 let r=condition(unknown,ctx);
 assert.equal(r.status,"UNSUPPORTED");
 assert.deepEqual(r.missing_observed_fields,["opponent_hp_exact"]);
 const noBoosts=withHPAndBoosts();
 noBoosts.facts.opponent_boosts={source:"UNKNOWN"};
 r=condition(noBoosts,ctx);
 assert.deepEqual(r.missing_observed_fields,["opponent_boosts"]);
 const noStatus=withHPAndBoosts();
 noStatus.facts.opponent_status={source:"UNKNOWN"};
 r=condition(noStatus,ctx);
 assert.deepEqual(r.missing_observed_fields,["opponent_status"]);
 const onlyAttack=withHPAndBoosts();
 onlyAttack.facts.opponent_boosts=known({atk:1});
 r=condition(onlyAttack,ctx);
 assert.match(r.reason,/all seven/);
});
test("HP exceeding legal maximum and fainted state fail closed",()=>{
 const impossible=condition(withHPAndBoosts({hp:9999}),ctx);
 assert.equal(impossible.status,"UNSUPPORTED");
 assert.equal(impossible.candidate_count,undefined);
 assert.equal(impossible.rejected.length,4);
 assert.match(impossible.reason,/no supported legal/);
 const faint=condition(withHPAndBoosts({hp:0}),ctx);
 assert.equal(faint.status,"UNSUPPORTED");
 assert.match(faint.reason,/fainted/);
});
test("undisclosed or contradictory screen duration cannot be filled from simulator defaults",()=>{
 const obs=withHPAndBoosts();
 obs.facts.screens=known(["reflect"]);
 const missing=condition(obs,ctx);
 assert.equal(missing.status,"UNSUPPORTED");
 assert.deepEqual(missing.missing_observed_fields,["screen_remaining_turns"]);
 obs.facts.screen_remaining_turns=known({reflect:0});
 assert.equal(condition(obs,ctx).status,"UNSUPPORTED");
 obs.facts.screen_remaining_turns=known({lightscreen:3});
 assert.equal(condition(obs,ctx).status,"UNSUPPORTED");
 obs.facts.screens=known([]);
 assert.equal(condition(obs,ctx).status,"UNSUPPORTED");
});
test("unresolved choice, Mega history and weather are not fabricated",()=>{
 const obs=withHPAndBoosts();
 obs.facts.request_state=known("switch");
 assert.match(condition(obs,ctx).reason,/move-request/);
 obs.facts.request_state=known("move");
 obs.facts.weather=known("raindance");
 assert.match(condition(obs,ctx).reason,/weather/);
 obs.facts.weather=known("");
 obs.facts.pending_choice=known("move 1");
 assert.match(condition(obs,ctx).reason,/queued/);
 obs.facts.pending_choice={source:"UNKNOWN"};
 obs.facts.opponent_mega_state=known(true);
 // The existing observation validator detects incompatible base form.
 assert.throws(()=>condition(obs,ctx),/Mega state conflicts/);
});
test("synthetic evidence cannot be mislabeled as real-game proof; even verified provenance stays provisional",()=>{
 const obs=withHPAndBoosts();
 obs.facts.opponent_hp_exact={source:"GAME_OBSERVED",value:100,
   evidence_ids:["fixture-CONDITION"]};
 assert.throws(()=>condition(obs,ctx),/game observation lacks verified/);
 const real=withHPAndBoosts();
 real.origin="REAL_GAME";real.verification="USER_VERIFIED";
 real.evidence=[{id:"game-frame-1",kind:"GAME_FRAME"}];
 for(const [key,fact] of Object.entries(real.facts)){
  if(fact.source==="SYNTHETIC"){
   real.facts[key]={source:"GAME_OBSERVED",value:structuredClone(fact.value),
     evidence_ids:["game-frame-1"]};
  }
 }
 const r=condition(real,ctx);
 assert.equal(r.status,"PROVISIONAL_CONDITIONAL_SYNTHETIC");
 assert.equal(r.game_compatibility_verified,false);
 assert.equal(r.real_game_partial_state_restored,false);
 assert.equal(r.candidate_count,4);
});
