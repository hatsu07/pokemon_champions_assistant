"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {run}=require("./champions_weather_speed_probe.cjs");
const {checkWeatherEvidence}=require("./champions_weather_gate_a_contract.cjs");
test("weather source evidence rejects forged game parity, modified lifecycle and unknown rounding",()=>{
 const observed=run();
 assert.deepEqual(checkWeatherEvidence(observed),{
   matches:12,rounding_unknown:1,source_agreed_unexecuted:1,same_weather_reapply_unexecuted:1,
   executed_lifecycle_cases:1,scope:"PINNED_SYNTHETIC_WEATHER_NOT_GAME"
 });
 for(const change of [
   x=>{x.gate_A="CONFIRMED";},
   x=>{x.game_verified_turns=1;},
   x=>{x.private_golden_read=true;},
   x=>{x.live_use_approved=true;},
   x=>{x.source_evidence="OFFICIAL_DEVICE";},
   x=>{x.counts.SOURCE_SPEC_MATCH=13;},
   x=>{x.results[0].status="GAME_OBSERVED_MATCH";},
   x=>{x.results[12].status="SOURCE_SPEC_MATCH";},
   x=>{x.results[12].expected_speed=321;},
   x=>{x.results[0].observed_first="p2";},
   x=>{x.results[0].executed_moves.p1.id="protect";},
   x=>{x.results[0].pinned_speed=1;},
   x=>{x.results[0].synthetic_initial_duration=8;},
   x=>{x.results[0].provenance="GAME_OBSERVED";},
   x=>{x.lifecycle.after_turn_remaining=[7,6,5,4,3];},
   x=>{x.lifecycle.override_weather_event="";},
   x=>{x.lifecycle.actual_game_proven=true;},
   x=>{x.weather_duration_rules[1].status="GAME_VERIFIED";},
   x=>{x.weather_duration_rules[1].pinned_tested=true;},
   x=>{x.weather_duration_rules[0].operation="USE_HEAT_ROCK";},
   x=>{x.duration_conflict={status:"SOURCE_CONFLICT"};},
   x=>{x.lifecycle.executed_move_order=["p2","p1"];},
   x=>{x.lifecycle.executed_moves.p1.id="sunnyday";},
   x=>{x.lifecycle.executed_moves.p2.name="Rain Dance";},
   x=>{x.lifecycle.executed_moves.p1.priority=1;},
   x=>{x.lifecycle.executed_move_events[0]="|move|p2a: Metagross|Rain Dance|p2a: Metagross";},
   x=>{x.lifecycle.executed_move_events.pop();},
   x=>{x.lifecycle.weather_change_events.reverse();},
   x=>{x.unverified[1]="Weather extenders game-confirmed";},
   x=>{x.results.pop();}
 ]){
   const changed=structuredClone(observed);change(changed);
   assert.throws(()=>checkWeatherEvidence(changed),/weather Gate A/);
 }
});
