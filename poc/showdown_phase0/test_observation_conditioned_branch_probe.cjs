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
 // The narrow conditional probe needs confirmed absence of unimplemented
 // effects and the unchanged form's current type, never UNKNOWN defaults.
 obs.facts.terrain=known("");
 obs.facts.hazards=known([]);
 obs.facts.opponent_current_type=known(
   [...ctx.dex.species.get(obs.facts.opponent_form.value).types]);
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
 const unknown=withHPAndBoosts();
 unknown.facts.opponent_hp_exact={source:"UNKNOWN"};
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
test("observed Sitrus Berry consumption conditions only its original legal holder",()=>{
 const obs=withHPAndBoosts({hp:70,atk:1});
 obs.facts.opponent_item=known("");
 obs.facts.opponent_consumed_item=known("sitrusberry");
 const hash=fingerprint(obs),r=condition(obs,ctx);
 assert.equal(fingerprint(obs),hash,"observed item history must be immutable");
 assert.equal(r.status,"PROVISIONAL_CONDITIONAL_SYNTHETIC");
 assert.equal(r.candidate_count,1);
 assert.deepEqual(r.candidates.map(c=>c.id),["B"]);
 assert.equal(r.candidates[0].original_hypothesis_item,"sitrusberry");
 assert.equal(r.candidates[0].conditioned_item,"");
 assert.equal(r.candidates[0].consumed_item_observation,"sitrusberry");
 assert.equal(r.candidates[0].item_history_scope,
  "CONDITIONED_EMPTY_ITEM_ONLY_NO_CONSUMPTION_EFFECT_OR_LOG_REPLAY");
 assert.equal(r.candidates[0].conditioned_hp,70,"no Berry heal effect may be invented");
 assert.ok(r.admitted_evidence_fields.includes("opponent_consumed_item"));
 assert.ok(r.unrecovered_hidden_fields.includes("internal_rng"));
 assert.equal(r.not_a_unique_true_state,true,
  "one matching fixed fixture does not prove the true hidden opponent set");
 assert.equal(r.game_compatibility_verified,false);
 assert.equal(r.real_game_partial_state_restored,false);
 assert.equal(r.live_recommendation_permitted,false);
 const repeated=condition(obs,ctx);
 assert.deepEqual(r.candidates.map(x=>x.conditioned_sha256),
  repeated.candidates.map(x=>x.conditioned_sha256));
 assert.deepEqual(r.candidates.map(x=>x.continuation_sha256),
  repeated.candidates.map(x=>x.continuation_sha256));
});
test("consumed-item evidence cannot override unknown or nonempty current item",()=>{
 for(const value of [{source:"UNKNOWN"},known("sitrusberry")]){
  const obs=withHPAndBoosts();
  obs.facts.opponent_consumed_item=known("sitrusberry");
  obs.facts.opponent_item=value;
  const r=condition(obs,ctx);
  assert.equal(r.status,"UNSUPPORTED");
  assert.match(r.reason,/requires observed empty current item/);
 }
});
test("only Champions one-use Berries are accepted as consumed; nonconsumable and absent holders fail",()=>{
 for(const item of ["choicescarf","leftovers"]){
  const obs=withHPAndBoosts({hp:70});
  obs.facts.opponent_item=known("");
  obs.facts.opponent_consumed_item=known(item);
  const r=condition(obs,ctx);
  assert.equal(r.status,"UNSUPPORTED",item);
  assert.match(r.reason,/only consumed one-use Berries supported/);
 }
 // Oran Berry is consumable in the Champions master, but none of the four
 // legal registered hypotheses originally held it. Do not fabricate a holder.
 const missing=withHPAndBoosts({hp:70});
 missing.facts.opponent_item=known("");
 missing.facts.opponent_consumed_item=known("oranberry");
 const denied=condition(missing,ctx);
 assert.equal(denied.status,"UNSUPPORTED");
 assert.equal(denied.rejected.length,4);
 assert.match(denied.reason,/no supported legal hypothesis/);
 const invalid=withHPAndBoosts();
 invalid.facts.opponent_item=known("");
 invalid.facts.opponent_consumed_item=known("imaginary_item");
 assert.throws(()=>condition(invalid,ctx),/consumed item absent from Champions master/);
});
test("a known consumed item plus an unrepresented effect history remains unsupported",()=>{
 const obs=withHPAndBoosts();
 obs.facts.opponent_item=known("");
 obs.facts.opponent_consumed_item=known("sitrusberry");
 obs.facts.effect_remaining_turns=known({encore:2});
 const r=condition(obs,ctx);
 assert.equal(r.status,"UNSUPPORTED");
 assert.match(r.reason,/known hidden-history field/);
});
test("known terrain, hazards and changed types fail closed rather than contradict the Battle",()=>{
 const obs=withHPAndBoosts();
 for(const [field,value,pattern] of [
   ["terrain","electricterrain",/terrain/],
   ["hazards",["stealthrock"],/hazards/],
   ["opponent_current_type",["Fairy"],/current type/],
   ["opponent_current_type","Dragon",/current type/]
 ]){
   const altered=structuredClone(obs);
   altered.facts[field]=known(value);
   const before=fingerprint(altered),r=condition(altered,ctx);
   assert.equal(r.status,"UNSUPPORTED",field);
   assert.match(r.reason,pattern);
   assert.equal(r.live_recommendation_permitted,false);
   assert.equal(fingerprint(altered),before,"must not rewrite known observations");
 }
});
test("unknown terrain/hazards/type must not become default absence or base type",()=>{
 for(const field of ["terrain","hazards","opponent_current_type"]){
   const obs=withHPAndBoosts();
   obs.facts[field]={source:"UNKNOWN"};
   const r=condition(obs,ctx);
   assert.equal(r.status,"UNSUPPORTED",field);
   assert.deepEqual(r.missing_observed_fields,[field]);
 }
});
// Consumed Berries are handled only with a separately observed current empty item.
test("known but unmodeled effect history is not silently ignored",()=>{
 for(const [field,value] of [
  ["effect_remaining_turns",{leechseed:2}],
  ["opponent_mega_history",{previous_form:"garchomp"}],
  ["choice_lock","earthquake"],
  ["action_history",["switch"]]
 ]){
   const obs=withHPAndBoosts();
   obs.facts[field]=known(value);
   const r=condition(obs,ctx);
   assert.equal(r.status,"UNSUPPORTED",field);
   assert.match(r.reason,/known hidden-history field/);
 }
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
