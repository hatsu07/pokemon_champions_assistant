"use strict";
// Phase-0 intake triage. Never decide the correct Champions rule from Showdown,
// and NEVER treat a partial observation as a complete Battle for LIVE decisions.
const {secondaryComparison}=require("./champions_damage_rounding_secondary_reference.cjs");
const PARTIAL_SCHEMA=require("./partial_observation_schema.json");
const PARTIAL_KEYS=PARTIAL_SCHEMA.properties.facts.required;
const GATES=Object.freeze({gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",phase1:"NO_GO"});
const CURRENT_REQUIRED=Object.freeze([
 "request_state","active_opponent","opponent_hp_exact","opponent_boosts",
 "opponent_status","opponent_current_type","weather","terrain","hazards","screens"
]);
const HISTORY=["opponent_mega_history","opponent_removed_item",
  "opponent_consumed_item","choice_lock","opponent_remaining_pp"];
const SOURCE=new Set(["UNKNOWN","INFERRED","SYNTHETIC","GAME_OBSERVED"]);
const isObject=x=>!!x&&typeof x==="object"&&!Array.isArray(x);
function validRef(e){
 return isObject(e)&&["GAME_FRAME","GAME_LOG"].includes(e.kind)&&
   typeof e.id==="string"&&!!e.id&&
   typeof e.match_id==="string"&&!!e.match_id&&
   typeof e.active_instance_id==="string"&&!!e.active_instance_id&&
   Number.isInteger(e.turn_epoch)&&e.turn_epoch>=0&&
   Number.isInteger(e.state_revision)&&e.state_revision>=0;
}
const sameFrame=(a,b)=>a&&b&&a.id===b.id&&a.match_id===b.match_id&&
 a.turn_epoch===b.turn_epoch&&a.state_revision===b.state_revision&&
 a.active_instance_id===b.active_instance_id;
function basicFactCheck(doc){
 if(!isObject(doc)||doc.schema!=="phase0-partial-observation-v1"||
    !["REAL_GAME","SYNTHETIC"].includes(doc.origin)||
    !["USER_VERIFIED","UNVERIFIED"].includes(doc.verification)||
    !Array.isArray(doc.evidence)||!isObject(doc.facts)||
    PARTIAL_KEYS.some(k=>!isObject(doc.facts[k])||!SOURCE.has(doc.facts[k].source))||
    Object.keys(doc.facts).length!==PARTIAL_KEYS.length)
   throw Error("partial observation structural contract invalid");
 if(doc.origin!=="REAL_GAME"&&doc.verification==="USER_VERIFIED")
   throw Error("synthetic origin cannot assert game verified");
 const ids=new Set();
 for(const e of doc.evidence){
   if(typeof e?.id!=="string"||!e.id||ids.has(e.id))
     throw Error("duplicate/invalid evidence id");
   ids.add(e.id);
 }
 for(const k of PARTIAL_KEYS){
   const f=doc.facts[k];
   if(f.source==="UNKNOWN"&&(Object.hasOwn(f,"value")||Object.hasOwn(f,"evidence_ids")))
     throw Error("UNKNOWN fact cannot contain value/evidence: "+k);
   if(f.source==="INFERRED"&&(!Object.hasOwn(f,"value")||f.evidence_ids))
     throw Error("inference cannot pretend to have evidence: "+k);
   if(f.source==="GAME_OBSERVED"&&
     (doc.origin!=="REAL_GAME"||doc.verification!=="USER_VERIFIED"))
     throw Error("unverified GAME_OBSERVED: "+k);
   if(["GAME_OBSERVED","SYNTHETIC"].includes(f.source)&&
      (!Object.hasOwn(f,"value")||!Array.isArray(f.evidence_ids)||
       f.evidence_ids.length!==1||!ids.has(f.evidence_ids[0])))
     throw Error("observed fact requires one linked evidence id: "+k);
 }
}
const sameKeys=(value,keys)=>isObject(value)&&
 Object.keys(value).sort().join(",")===keys.slice().sort().join(",");
const validDuration=x=>Number.isInteger(x)&&x>=1&&x<=8;
const validScreens=s=>Array.isArray(s)&&s.length<=2&&
 new Set(s).size===s.length&&s.every(x=>["reflect","lightscreen"].includes(x));
// Mirror the LIMITED supported current-state envelope of #172, not its
// Battle creation. A shared evidence ID cannot make inconsistent values valid.
function invalidCurrentFieldValues(f,frame,statuses){
 const linked=k=>statuses[k]==="LINKED_CURRENT_GAME_FACT";
 const invalid=new Set();
 if(linked("request_state")&&f.request_state.value!=="move")
   invalid.add("request_state");
 if(linked("hazards")){
   const value=f.hazards.value;
   if(!sameKeys(value,["side","effects","turn_epoch","state_revision"])||
     value.side!=="p2"||value.turn_epoch!==frame.turn_epoch||
     value.state_revision!==frame.state_revision||
     !Array.isArray(value.effects)||value.effects.length>1||
     (value.effects.length===1&&value.effects[0]!=="stealthrock"))
     invalid.add("hazards");
 }
 if(linked("screens")&&!validScreens(f.screens.value))
   invalid.add("screens");
 if(linked("weather")&&!["","raindance"].includes(f.weather.value))
   invalid.add("weather");
 if(linked("terrain")&&!["","electricterrain"].includes(f.terrain.value))
   invalid.add("terrain");
 if(linked("screens")&&validScreens(f.screens.value)){
   const active=f.screens.value;
   const durationKnown=linked("screen_remaining_turns");
   if(active.length){
     if(durationKnown&&(!sameKeys(f.screen_remaining_turns.value,active)||
         active.some(k=>!validDuration(f.screen_remaining_turns.value[k]))))
       invalid.add("screen_remaining_turns");
   }else if(durationKnown&&!sameKeys(f.screen_remaining_turns.value,[])){
     invalid.add("screen_remaining_turns");
   }
 }
 const fieldListValid=linked("weather")&&linked("terrain")&&
   ["","raindance"].includes(f.weather.value)&&
   ["","electricterrain"].includes(f.terrain.value);
 if(fieldListValid){
   const active=[...(f.weather.value?["weather"]:[]),
     ...(f.terrain.value?["terrain"]:[])];
   if(linked("effect_remaining_turns")&&
      (!sameKeys(f.effect_remaining_turns.value,active)||
       active.some(k=>!validDuration(f.effect_remaining_turns.value[k]))))
     invalid.add("effect_remaining_turns");
 }
 return [...invalid];
}
function triagePartialBoard(doc){
 basicFactCheck(doc);
 const refs=new Map(doc.evidence.map(x=>[x.id,x]));
 const req=doc.facts.request_state;
 const current=req.source==="GAME_OBSERVED"&&
   doc.origin==="REAL_GAME"&&doc.verification==="USER_VERIFIED"?
   refs.get(req.evidence_ids?.[0]):null;
 const currentValid=validRef(current);
 const statuses=Object.fromEntries(PARTIAL_KEYS.map(key=>{
   const f=doc.facts[key];
   if(f.source==="UNKNOWN")return [key,"UNKNOWN"];
   if(f.source==="INFERRED")return [key,"INFERRED_NOT_CURRENT_GAME_EVIDENCE"];
   if(f.source==="SYNTHETIC")return [key,"SYNTHETIC_ONLY"];
   const e=refs.get(f.evidence_ids[0]);
   if(!currentValid||!validRef(e))
     return [key,"UNBOUND_OR_UNDATED_GAME_EVIDENCE"];
   if(HISTORY.includes(key))
     return [key,e.match_id===current.match_id&&
       e.active_instance_id===current.active_instance_id?
       "HISTORY_REQUIRES_EVENT_AND_STATE_TRANSITION_CHECK":"CROSS_SUBJECT_OR_MATCH_EVIDENCE"];
   return [key,sameFrame(e,current)?"LINKED_CURRENT_GAME_FACT":
     "STALE_OR_CROSS_FRAME_GAME_FACT"];
 }));
 const invalid=currentValid?invalidCurrentFieldValues(doc.facts,current,statuses):[];
 for(const key of invalid)statuses[key]="INVALID_CURRENT_STATE_EVIDENCE";
 const blockers=CURRENT_REQUIRED.filter(k=>statuses[k]!=="LINKED_CURRENT_GAME_FACT");
 const screen=doc.facts.screens;
 if(statuses.screens==="LINKED_CURRENT_GAME_FACT"&&
   Array.isArray(screen.value)&&screen.value.length>0&&
   statuses.screen_remaining_turns!=="LINKED_CURRENT_GAME_FACT")
   blockers.push("screen_remaining_turns");
 const weather=doc.facts.weather,terrain=doc.facts.terrain;
 if((statuses.weather==="LINKED_CURRENT_GAME_FACT"&&weather.value!==""||
     statuses.terrain==="LINKED_CURRENT_GAME_FACT"&&terrain.value!=="")&&
    statuses.effect_remaining_turns!=="LINKED_CURRENT_GAME_FACT")
   blockers.push("effect_remaining_turns");
 // A linked field is still only the caller's evidence assertion; it does not
 // prove source authenticity, mechanics fidelity, hidden state or unique truth.
 return {schema:"phase0-targeted-evidence-triage-v1",kind:"GATE_B_BOARD",
  ...GATES,scope:"READ_ONLY_PROVENANCE_COVERAGE_NOT_RECONSTRUCTION",
  current_request_evidence_linked:!!currentValid,
  statuses,required_current_fields:[...CURRENT_REQUIRED],
  invalid_current_state_fields:invalid,
  missing_or_unbound_current_fields:[...new Set(blockers)],
  observation_readiness: invalid.length?"INVALID_CURRENT_STATE_EVIDENCE":
   blockers.length?"INSUFFICIENT_CURRENT_OBSERVATION":
   "CURRENT_FACTS_LINKED_BUT_HIDDEN_BATTLE_UNRECOVERED",
  hidden_state_not_recovered:true,full_battle_restored:false,
  observations_independently_authenticated:false,
  live_use_permitted:false,raw_evidence_in_output:false};
}
const exactKeys=(x,keys)=>isObject(x)&&
 Object.keys(x).sort().join(",")===keys.slice().sort().join(",");
function triageRoundingDamage(input){
 const rows=secondaryComparison().rows;
 const combined=new Set(rows.map(r=>r.combined));
 const separate=new Set(rows.map(r=>r.sequential));
 const onlyCombined=[...combined].filter(n=>!separate.has(n));
 const onlySeparate=[...separate].filter(n=>!combined.has(n));
 const rollDifferences=rows.filter(r=>r.combined!==r.sequential).length;
 // For the CURRENT reviewed fixture both sets are identical despite four
 // RNG-position differences: no unknown-roll single hit can select a method.
 if(rows.length!==16||rollDifferences!==4||
    onlyCombined.length||onlySeparate.length)
   throw Error("reviewed fixture identifiability contract changed; re-review needed");
 const result={schema:"phase0-targeted-evidence-triage-v1",kind:"GATE_A_BURN_REFLECT",
  ...GATES,scope:"ONE_REVIEWED_SECONDARY_FINAL_MODIFIER_STEP_ONLY",
  classification:"INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE",
  fixture_identifiability:"NOT_IDENTIFIABLE_WITH_UNKNOWN_RNG_FOR_THIS_FIXTURE",
  reviewed_roll_position_difference_count:rollDifferences,
  combined_only_integer_delta_count:onlyCombined.length,
  sequential_only_integer_delta_count:onlySeparate.length,
  single_unknown_roll_hit_can_discriminate:false,
  resolves_champions_rounding:false,independent_rng_roll_observed:false,
  full_damage_formula_verified:false,observations_independently_authenticated:false,
  live_use_permitted:false,raw_evidence_in_output:false};
 if(input==null)return {...result,reason:"no independent game observation supplied"};
 // Strict *declared* provenance. Even with a valid record the original image,
 // sequence, target stats and effects are NOT independently authenticated.
 const fields=["schema","origin","verification","evidence","scenario",
    "before_hp","after_hp","conditions"];
 if(!exactKeys(input,fields)||input.schema!=="phase0-rounding-game-candidate-v1"||
   input.origin!=="REAL_GAME"||input.verification!=="USER_VERIFIED"||
   !exactKeys(input.evidence,["before","after"])||
   !validRef(input.evidence.before)||!validRef(input.evidence.after)||
   input.evidence.before.id===input.evidence.after.id||
   input.evidence.before.match_id!==input.evidence.after.match_id||
   input.evidence.before.active_instance_id!==input.evidence.after.active_instance_id||
   input.evidence.before.turn_epoch!==input.evidence.after.turn_epoch||
   input.evidence.after.state_revision<=input.evidence.before.state_revision||
   input.scenario!=="GARCHOMP_EARTHQUAKE_BLASTOISE_BURN_REFLECT_NONCRITICAL"||
   !exactKeys(input.conditions,["burn","reflect","noncritical",
       "no_weather","no_items","no_boosts","exact_one_hit_hp",
       "no_intermediate_hp_change","reviewed_fixture_stats_confirmed",
       "reviewed_abilities_confirmed","move_hit_confirmed"])||
   Object.values(input.conditions).some(v=>v!==true)||
   !Number.isSafeInteger(input.before_hp)||input.before_hp<=0||
   !Number.isSafeInteger(input.after_hp)||input.after_hp<0||
   input.after_hp>=input.before_hp)
   return {...result,reason:"case provenance, exact HP, or all relevant independent-condition assertions missing"};
 const delta=input.before_hp-input.after_hp;
 const inCombined=combined.has(delta),inSeparate=separate.has(delta);
 const classification=inCombined&&inSeparate?
   "NOT_IDENTIFIABLE_WITH_UNKNOWN_RNG_FOR_THIS_FIXTURE":
   "OUTSIDE_BOTH_REVIEWED_METHODS_RECHECK_CONDITIONS";
 return {...result,classification,
  basis:"USER_DECLARED_MATCH_AND_CONDITIONS_NOT_IMAGE_AUTHENTICATED",
  // Never export actual HP, case IDs, participant IDs or evidence links.
  observed_delta_matches_combined_set:inCombined,
  observed_delta_matches_separate_set:inSeparate,
  unknown_roll_handled_as_all_sixteen:true,
  discriminates_reviewed_two_method_sets:false,
  reason:"the two reviewed integer-damage sets are identical across unknown RNG rolls; one hit cannot identify the rounding method for this fixture"};
}
function runCli(argv=process.argv.slice(2)){
 if(argv.length===1&&argv[0]==="--empty")return triageRoundingDamage(null);
 if(argv.length!==2||!["--damage","--partial"].includes(argv[0]))
   throw Error("usage: --empty OR --damage local.json OR --partial local.json");
 const fs=require("node:fs");
 // Only read bytes locally. No raw evidence is ever written to output.
 const input=JSON.parse(fs.readFileSync(argv[1],"utf8"));
 return argv[0]==="--damage"?triageRoundingDamage(input):triagePartialBoard(input);
}
if(require.main===module){
 try{
  process.stdout.write(JSON.stringify(runCli())+"\n");
 }catch(err){
  // I/O errors may include the user's private local path. Never emit any
  // raw exception message or user-supplied value from the local-only CLI.
  process.stdout.write(JSON.stringify({status:"NOT_RUN",
    reason:err instanceof SyntaxError?"MALFORMED_LOCAL_JSON":"INPUT_REJECTED_OR_UNREADABLE",
    ...GATES,live_use_permitted:false})+"\n");
  process.exitCode=2;
 }
}
module.exports={triagePartialBoard,triageRoundingDamage,runCli,CURRENT_REQUIRED,GATES};
