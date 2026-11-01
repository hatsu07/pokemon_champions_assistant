"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {LEDGER,SOURCES,SPECIES,CASES,checkLedger,independentSpeeds,masterCheck,reference}=
 require("./champions_weather_speed_reference.cjs");
const {run}=require("./champions_weather_speed_probe.cjs");
test("weather source strength, two distinct unexecuted duration rules and unknown mechanics fail closed",()=>{
 assert.equal(checkLedger(),true);
 assert.equal(LEDGER.weather_default_turns,5);
 assert.equal(LEDGER.weather_duration_rules[0].status,"SECONDARY_SOURCE_DESCRIBED_NOT_EXECUTED");
 assert.equal(LEDGER.weather_duration_rules[0].operation,"REAPPLY_SAME_WEATHER_MOVE_WHILE_ACTIVE");
 assert.equal(LEDGER.weather_duration_rules[1].status,"SECONDARY_SOURCES_AGREE_NOT_EXECUTED");
 assert.equal(LEDGER.weather_duration_rules[1].operation,"ACTIVATE_SUNNY_WEATHER_WITH_HEAT_ROCK");
 assert.equal(LEDGER.weather_duration_rules[1].expected_turns,8);
 assert.equal(LEDGER.weather_duration_rules[1].pinned_tested,false);
 assert.equal(LEDGER.weather_duration_rules[1].game_verified,false);
 assert.equal(masterCheck(),4);
 for(const corrupt of [
   l=>{l.source[0][2]="game device certified";},
   l=>{l.source[0][1]="https://not-original.example";},
   l=>{l.weather_duration_rules[1].status="GAME_VERIFIED";},
   l=>{l.weather_duration_rules[1].pinned_tested=true;},
   l=>{l.weather_duration_rules[0].expected_effect="RESETS_TIMER";},
   l=>{l.weather_duration_rules[1].source_ids.pop();},
   l=>{l.weather_duration_rules[1].game_verified=true;},
   l=>{l.source_conflicts=[{status:"SOURCE_CONFLICT"}];},
   l=>{l.unverified[0]="Real-game weather confirmed";},
   l=>{l.reviewed_species[0][5]=87;},
   l=>{l.scenarios[1][7]="p1";},
   l=>{l.weather_default_turns=8;},
   l=>{l.device_turns=1;},
   l=>{l.private_golden_read=true;},
   l=>{l.live_use_approved=true;}
 ]){
   const candidate=structuredClone(LEDGER);corrupt(candidate);
   assert.throws(()=>checkLedger(candidate),/weather source evidence contract drift/);
 }
 assert.equal(SOURCES.length,5);
 assert.equal(SPECIES.length,4);
 assert.equal(CASES.length,13);
});
test("integer speed reference recognizes invariant and unresolved compounded rounding",()=>{
 const calc=(base,weatherBoost,par=false,scarf=false,stage=0)=>
   independentSpeeds({base,weatherBoost,par,scarf,stage});
 assert.deepEqual(calc(107,true).possible,[214]);
 assert.deepEqual(calc(107,false).possible,[107]);
 assert.deepEqual(calc(110,true).possible,[220]);
 assert.deepEqual(calc(102,true).possible,[204]);
 assert.deepEqual(calc(72,true).possible,[144]);
 assert.deepEqual(calc(72,true,true).possible,[72]);
 assert.deepEqual(calc(110,true,false,false,1).possible,[330]);
 assert.deepEqual(calc(107,true,false,true).possible,[320,321]);
 assert.equal(calc(107,true,false,true).effective,null);
 assert.throws(()=>calc(107,true,false,false,-1),/UNSUPPORTED/);
 assert.throws(()=>independentSpeeds({base:107,weatherBoost:true,par:false,scarf:false,stage:0,sun:true}),/UNSUPPORTED/);
});
test("reviewed species weather and AP assumptions produce expected order",()=>{
 const refs=CASES.map(reference);
 assert.deepEqual(refs.map(r=>r.expected_first),CASES.map(c=>c[7]));
 assert.equal(refs[0].base_speed,107);
 assert.equal(refs[0].opponent_speed,124);
 assert.equal(refs[3].base_speed,110);
 assert.equal(refs[5].base_speed,102);
 assert.equal(refs[7].base_speed,72);
 assert.equal(refs[12].expected_speed,null);
 assert.deepEqual(refs[12].possible_speeds,[320,321]);
 assert.throws(()=>reference(structuredClone(CASES[0])),/not reviewed/);
});
test("pinned synthetic 6 registered/3 selected weather order and 5-turn overwriting",()=>{
 const r=run();
 assert.equal(r.schema,"phase0-champions-weather-speed-diagnostic-v1");
 assert.equal(r.pinned_sha,"ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
 assert.equal(r.format,"gen9championsbssregmc");
 assert.equal(r.tested_species_count,4);
 assert.equal(r.case_count,13);
 assert.deepEqual(r.counts,{SOURCE_SPEC_MATCH:12,SOURCE_SPEC_MISMATCH:0,
   SOURCE_UNKNOWN_MODIFIER_ORDER:1});
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.game_verified_turns,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 assert.equal(r.weather_duration_rules[0].status,"SECONDARY_SOURCE_DESCRIBED_NOT_EXECUTED");
 assert.equal(r.weather_duration_rules[1].status,"SECONDARY_SOURCES_AGREE_NOT_EXECUTED");
 assert.equal(r.weather_duration_rules[1].pinned_tested,false);
 assert.equal(Object.hasOwn(r,"duration_conflict"),false);
 assert.equal(r.lifecycle.status,"SOURCE_SPEC_MATCH");
 assert.ok(r.lifecycle.first_weather_event.includes("RainDance"));
 assert.ok(r.lifecycle.override_weather_event.includes("SunnyDay"));
 assert.deepEqual(r.lifecycle.executed_move_order,["p1","p2"]);
 assert.deepEqual(r.lifecycle.executed_moves,{
   p1:{id:"raindance",name:"Rain Dance",priority:0},
   p2:{id:"sunnyday",name:"Sunny Day",priority:0}
 });
 assert.deepEqual(r.lifecycle.executed_move_events,[
   "|move|p1a: Garchomp|Rain Dance|p1a: Garchomp",
   "|move|p2a: Metagross|Sunny Day|p2a: Metagross"
 ]);
 assert.deepEqual(r.lifecycle.weather_change_events,[
   "|-weather|RainDance","|-weather|SunnyDay"
 ]);
 assert.deepEqual(r.lifecycle.after_turn_remaining,[4,3,2,1,0]);
 assert.equal(r.lifecycle.extended_item_evaluated,false);
 assert.equal(r.lifecycle.actual_game_proven,false);
 for(const [i,actual] of r.results.entries()){
   const ref=reference(CASES[i]);
   assert.equal(actual.id,ref.id);
   assert.equal(actual.species,ref.species);
   assert.equal(actual.ability,ref.ability);
   assert.equal(actual.weather,ref.weather);
   assert.equal(actual.base_speed,ref.base_speed);
   assert.deepEqual(actual.possible_speeds,ref.possible_speeds);
   assert.equal(actual.expected_speed,ref.expected_speed);
   assert.equal(actual.opponent_speed,124);
   assert.ok(ref.possible_speeds.includes(actual.pinned_speed));
   assert.equal(actual.executed_moves.p1.name,"Swords Dance");
   assert.equal(actual.executed_moves.p1.id,"swordsdance");
   assert.equal(actual.executed_moves.p2.name,"Swords Dance");
   assert.equal(actual.executed_moves.p2.id,"swordsdance");
   assert.deepEqual(actual.observed_sequence,[actual.observed_first,
     actual.observed_first==="p1"?"p2":"p1"]);
   assert.equal(actual.provenance,"SYNTHETIC_PRETURN_WEATHER_STATUS_BOOST_NOT_DEVICE");
   assert.ok(actual.executed_seed>=1&&actual.executed_seed<=48);
   if(i!==12){
     assert.equal(actual.status,"SOURCE_SPEC_MATCH");
     assert.equal(actual.observed_first,ref.expected_first);
   }else{
     assert.equal(actual.status,"SOURCE_UNKNOWN_MODIFIER_ORDER");
     assert.equal(actual.expected_speed,null);
   }
 }
});
