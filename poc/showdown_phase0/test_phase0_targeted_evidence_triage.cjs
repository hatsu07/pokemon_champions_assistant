"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {secondaryComparison}=require("./champions_damage_rounding_secondary_reference.cjs");
const {triagePartialBoard,triageRoundingDamage,CURRENT_REQUIRED,GATES}=
  require("./phase0_targeted_evidence_triage.cjs");
const partialSchema=require("./partial_observation_schema.json");
const facts=()=>Object.fromEntries(partialSchema.properties.facts.required.map(k=>
  [k,{source:"UNKNOWN"}]));
const frame=(id="fixture-frame")=>({id,kind:"GAME_FRAME",
  match_id:"PRIVATE_TEST_ONLY_MATCH_DO_NOT_ECHO",
  active_instance_id:"p2-hidden-mon-2",turn_epoch:4,state_revision:42});
function board(){
 const a=frame(),f=facts();
 const bind=(k,value)=>f[k]={source:"GAME_OBSERVED",value,evidence_ids:[a.id]};
 for(const [k,v] of Object.entries({
   request_state:"move",active_opponent:"garchomp",
   opponent_hp_exact:70,opponent_boosts:{atk:0,def:0,spa:0,spd:0,spe:0,accuracy:0,evasion:0},
   opponent_status:"",opponent_current_type:["Dragon","Ground"],
   weather:"",terrain:"",hazards:{side:"p2",effects:[],
     turn_epoch:4,state_revision:42},screens:[]}))
   bind(k,v);
 return {schema:"phase0-partial-observation-v1",origin:"REAL_GAME",
   verification:"USER_VERIFIED",evidence:[a],facts:f};
}
function damage(){
 return {schema:"phase0-rounding-game-candidate-v1",origin:"REAL_GAME",
   verification:"USER_VERIFIED",evidence:{
     before:{...frame("before"),state_revision:41},
     after:frame("after")},
   scenario:"GARCHOMP_EARTHQUAKE_BLASTOISE_BURN_REFLECT_NONCRITICAL",
   before_hp:170,after_hp:150,
   conditions:{
     burn:true,reflect:true,noncritical:true,no_weather:true,
     no_items:true,no_boosts:true,exact_one_hit_hp:true,
     no_intermediate_hp_change:true,reviewed_fixture_stats_confirmed:true,
     reviewed_abilities_confirmed:true,move_hit_confirmed:true}};
}
const bound=(r)=>Object.values(r).every(x=>typeof x!=="string"||
 !x.includes("PRIVATE_TEST_ONLY_MATCH"));
test("no game damage observation and no private data gives NO_GO, not a match",()=>{
 const r=triageRoundingDamage(null);
 assert.deepEqual([r.gate_A,r.gate_B,r.phase1],
   [GATES.gate_A,GATES.gate_B,GATES.phase1]);
 assert.equal(r.classification,"INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE");
 assert.equal(r.resolves_champions_rounding,false);
 assert.equal(r.live_use_permitted,false);
});
test("all sixteen unknown RNG rolls, including overlap, are examined without device claims",()=>{
 const rows=secondaryComparison().rows;
 assert.equal(rows.length,16);
 assert.equal(rows.filter(x=>x.discriminating).length,4);
 const both=new Set(rows.map(x=>x.combined)),sep=new Set(rows.map(x=>x.sequential));
 for(const delta of new Set([...both,...sep,1000])){
  const d=damage();d.after_hp=d.before_hp-delta;
  if(d.after_hp<0)continue;
  const r=triageRoundingDamage(d);
  const expected=both.has(delta)&&sep.has(delta)?"INCONCLUSIVE_INTEGER_OVERLAP":
    both.has(delta)?"POTENTIAL_COMBINED_ONLY_UNDER_DECLARED_FIXTURE":
    sep.has(delta)?"POTENTIAL_SEPARATE_ONLY_UNDER_DECLARED_FIXTURE":
    "OUTSIDE_BOTH_REVIEWED_METHODS_RECHECK_CONDITIONS";
  assert.equal(r.classification,expected);
  assert.equal(r.unknown_roll_handled_as_all_sixteen,true);
  assert.equal(r.resolves_champions_rounding,false);
  assert.equal(r.full_damage_formula_verified,false);
  assert.equal(r.observations_independently_authenticated,false);
  assert.equal(r.raw_evidence_in_output,false);
  assert.ok(!JSON.stringify(r).includes("PRIVATE_TEST_ONLY_MATCH"));
 }
});
test("rounding source assumptions and pre/post identity fail closed",()=>{
 const cases=[
  d=>{d.verification="UNVERIFIED";},
  d=>{d.evidence.after.match_id="other";},
  d=>{d.evidence.after.active_instance_id="another";},
  d=>{d.evidence.after.kind="SYNTHETIC_FIXTURE";},
  d=>{d.evidence.after.turn_epoch=5;},
  d=>{d.evidence.after.state_revision=40;},
  d=>{d.evidence.after.id=d.evidence.before.id;},
  d=>{d.conditions.reviewed_fixture_stats_confirmed=false;},
  d=>{delete d.conditions.reviewed_abilities_confirmed;},
  d=>{d.conditions.move_hit_confirmed=false;},
  d=>{d.before_hp=d.after_hp;},
  d=>{d.evidence.after.state_revision="42";},
  d=>{d.evidence.untrusted_extra={};}
 ];
 for(const mutate of cases){
  const d=damage();mutate(d);
  const r=triageRoundingDamage(d);
  assert.equal(r.classification,"INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE");
  assert.equal(r.resolves_champions_rounding,false);
 }
});
test("complete linked board current facts still cannot reconstruct hidden state",()=>{
 const obs=board(),hash=JSON.stringify(obs);
 const r=triagePartialBoard(obs);
 assert.equal(JSON.stringify(obs),hash);
 assert.equal(r.observation_readiness,
   "CURRENT_FACTS_LINKED_BUT_HIDDEN_BATTLE_UNRECOVERED");
 assert.deepEqual(r.missing_or_unbound_current_fields,[]);
 for(const k of CURRENT_REQUIRED)
   assert.equal(r.statuses[k],"LINKED_CURRENT_GAME_FACT",k);
 assert.equal(r.full_battle_restored,false);
 assert.equal(r.hidden_state_not_recovered,true);
 assert.equal(r.gate_B,"UNSUPPORTED");
 assert.equal(r.observations_independently_authenticated,false);
 assert.ok(!JSON.stringify(r).includes("PRIVATE_TEST_ONLY_MATCH"));
});
test("stale field and partially known screens/terrain require current evidence",()=>{
 const obs=board();
 obs.evidence.push({...frame("old"),turn_epoch:3,state_revision:20});
 obs.facts.hazards.evidence_ids=["old"];
 obs.facts.screens.value=["reflect"];
 obs.facts.weather.value="raindance";
 const r=triagePartialBoard(obs);
 assert.ok(r.missing_or_unbound_current_fields.includes("hazards"));
 assert.ok(r.missing_or_unbound_current_fields.includes("screen_remaining_turns"));
 assert.ok(r.missing_or_unbound_current_fields.includes("effect_remaining_turns"));
 assert.equal(r.statuses.hazards,"STALE_OR_CROSS_FRAME_GAME_FACT");
 assert.equal(r.observation_readiness,"INSUFFICIENT_CURRENT_OBSERVATION");
 const guessed=board();
 guessed.facts.opponent_hp_exact={source:"INFERRED",value:70};
 assert.equal(triagePartialBoard(guessed).statuses.opponent_hp_exact,
   "INFERRED_NOT_CURRENT_GAME_EVIDENCE");
});
test("history is not replayed or promoted even with linked current-frame evidence",()=>{
 const obs=board();
 const id=obs.evidence[0].id;
 obs.facts.opponent_removed_item={source:"GAME_OBSERVED",
   value:{item_id:"choicescarf",cause:"knockoff"},evidence_ids:[id]};
 const r=triagePartialBoard(obs);
 assert.equal(r.statuses.opponent_removed_item,
   "HISTORY_REQUIRES_EVENT_AND_STATE_TRANSITION_CHECK");
 assert.equal(r.full_battle_restored,false);
 obs.evidence.push({...frame("past"),turn_epoch:2,state_revision:18});
 obs.facts.opponent_removed_item.evidence_ids=["past"];
 const r2=triagePartialBoard(obs);
 assert.equal(r2.statuses.opponent_removed_item,
   "HISTORY_REQUIRES_EVENT_AND_STATE_TRANSITION_CHECK");
 obs.evidence[1].active_instance_id="other-pokemon";
 assert.equal(triagePartialBoard(obs).statuses.opponent_removed_item,
   "CROSS_SUBJECT_OR_MATCH_EVIDENCE");
});
test("unknown or unauthenticated partial-board data cannot pretend game observation",()=>{
 const obs=board();
 obs.facts.hazards={source:"UNKNOWN",value:[]};
 assert.throws(()=>triagePartialBoard(obs),/UNKNOWN fact/);
 const second=board();
 second.origin="SYNTHETIC";
 assert.throws(()=>triagePartialBoard(second),/synthetic origin|unverified GAME_OBSERVED/);
 const third=board();
 third.facts.request_state.evidence_ids=["nonexistent"];
 assert.throws(()=>triagePartialBoard(third),/linked evidence/);
 const fake=board();
 fake.evidence[0].kind="SYNTHETIC_FIXTURE";
 assert.equal(triagePartialBoard(fake).observation_readiness,
   "INSUFFICIENT_CURRENT_OBSERVATION");
 const fourth=board();
 fourth.evidence[0].active_instance_id="";
 const r=triagePartialBoard(fourth);
 assert.equal(r.current_request_evidence_linked,false);
 assert.equal(r.observation_readiness,"INSUFFICIENT_CURRENT_OBSERVATION");
});

test("local CLI outputs only sanitized status and refuses malformed file input",()=>{
 const fs=require("node:fs"),os=require("node:os"),path=require("node:path");
 const {execFileSync}=require("node:child_process");
 const entry=path.join(__dirname,"phase0_targeted_evidence_triage.cjs");
 const blank=JSON.parse(execFileSync(process.execPath,[entry,"--empty"],{encoding:"utf8"}));
 assert.equal(blank.classification,"INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE");
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),"phase0-triage-only-"));
 try{
  const fp=path.join(dir,"fake-not-a-real-game.json");
  fs.writeFileSync(fp,JSON.stringify(damage()));
  const result=execFileSync(process.execPath,[entry,"--damage",fp],{encoding:"utf8"});
  const parsed=JSON.parse(result);
  assert.equal(parsed.kind,"GATE_A_BURN_REFLECT");
  assert.equal(parsed.live_use_permitted,false);
  assert.ok(!result.includes("PRIVATE_TEST_ONLY_MATCH"));
  assert.ok(!result.includes(fp));
  fs.writeFileSync(fp,JSON.stringify(board()));
  const boardResult=execFileSync(process.execPath,[entry,"--partial",fp],{encoding:"utf8"});
  assert.equal(JSON.parse(boardResult).kind,"GATE_B_BOARD");
  assert.ok(!boardResult.includes("PRIVATE_TEST_ONLY_MATCH"));
  assert.ok(!boardResult.includes(fp));
  const {spawnSync}=require("node:child_process");
  const missing=path.join(dir,"SECRET_LOCAL_PATH_DO_NOT_DISCLOSE.json");
  const failed=spawnSync(process.execPath,[entry,"--damage",missing],{encoding:"utf8"});
  assert.equal(failed.status,2);
  assert.equal(JSON.parse(failed.stdout).reason,"INPUT_REJECTED_OR_UNREADABLE");
  assert.ok(!failed.stdout.includes(missing));
  fs.writeFileSync(fp,"{PRIVATE_SENSITIVE_CONTENTS_MALFORMED");
  const malformed=spawnSync(process.execPath,[entry,"--partial",fp],{encoding:"utf8"});
  assert.equal(malformed.status,2);
  assert.equal(JSON.parse(malformed.stdout).reason,"MALFORMED_LOCAL_JSON");
  assert.ok(!malformed.stdout.includes(fp));
  assert.ok(!malformed.stdout.includes("PRIVATE_SENSITIVE_CONTENTS"));
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
