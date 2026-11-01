"use strict";
// Phase 0 evidence contract: only source-backed synthetic weather cases, not game compatibility.
const {LEDGER,CASES,WEATHER_DURATION_RULES,reference}=require("./champions_weather_speed_reference.cjs");
const {moveKey}=require("./champions_move_key.cjs");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
function checkWeatherEvidence(r){
 if(r?.schema!=="phase0-champions-weather-speed-diagnostic-v1"||
    r.pinned_sha!==PIN||r.format!=="gen9championsbssregmc"||r.regulation!=="M-C"||
    r.source_evidence!=="CHAMPIONS_FIXED_MASTER_AND_SECONDARY_SOURCE_NOT_GAME"||
    r.tested_species_count!==4||r.case_count!==13||r.results?.length!==13||
    r.gate_A!=="UNKNOWN"||r.gate_B!=="UNSUPPORTED"||r.adoption!=="NO_GO"||
    r.private_golden_read!==false||r.game_verified_turns!==0||
    r.live_use_approved!==false||r.source_only_not_game!==true||
    JSON.stringify(r.unverified)!==JSON.stringify(LEDGER.unverified)||
    Object.hasOwn(r,"duration_conflict")||
    JSON.stringify(r.weather_duration_rules)!==JSON.stringify(WEATHER_DURATION_RULES)||
    JSON.stringify(r.weather_duration_rules)!==JSON.stringify(LEDGER.weather_duration_rules)||
    r.lifecycle?.status!=="SOURCE_SPEC_MATCH"||
    r.lifecycle?.basis!=="TWO_EXECUTED_WEATHER_MOVES_AND_FOUR_FOLLOWUP_TURNS"||
    r.lifecycle?.extended_item_evaluated!==false||
    r.lifecycle?.actual_game_proven!==false||
    !r.lifecycle?.first_weather_event?.includes("RainDance")||
    !r.lifecycle?.override_weather_event?.includes("SunnyDay")||
    JSON.stringify(r.lifecycle?.executed_move_order)!==JSON.stringify(["p1","p2"])||
    JSON.stringify(r.lifecycle?.executed_moves)!==JSON.stringify({
      p1:{id:moveKey("Rain Dance"),name:"Rain Dance",priority:0},
      p2:{id:moveKey("Sunny Day"),name:"Sunny Day",priority:0}
    })||
    JSON.stringify(r.lifecycle?.executed_move_events)!==JSON.stringify([
      "|move|p1a: Garchomp|Rain Dance|p1a: Garchomp",
      "|move|p2a: Metagross|Sunny Day|p2a: Metagross"
    ])||
    JSON.stringify(r.lifecycle?.weather_change_events)!==JSON.stringify([
      "|-weather|RainDance","|-weather|SunnyDay"
    ])||
    JSON.stringify(r.lifecycle?.after_turn_remaining)!==JSON.stringify([4,3,2,1,0]))
    throw Error("weather Gate A evidence origin or duration changed");
 for(const [i,c] of CASES.entries()){
   const a=r.results[i],ref=reference(c),unknown=i===12;
   if(!a||a.id!==ref.id||a.ability!==ref.ability||
      a.species!==ref.species||a.weather!==ref.weather||
      a.base_speed!==ref.base_speed||a.expected_speed!==ref.expected_speed||
      a.opponent_speed!==ref.opponent_speed||
      JSON.stringify(a.possible_speeds)!==JSON.stringify(ref.possible_speeds)||
      !ref.possible_speeds.includes(a.pinned_speed)||
      a.status!==(unknown?"SOURCE_UNKNOWN_MODIFIER_ORDER":"SOURCE_SPEC_MATCH")||
      a.expected_first!==ref.expected_first||
      !["p1","p2"].includes(a.observed_first)||
      (!unknown&&a.observed_first!==ref.expected_first)||
      JSON.stringify(a.observed_sequence)!==
       JSON.stringify([a.observed_first,a.observed_first==="p1"?"p2":"p1"])||
      a.executed_moves?.p1?.id!==moveKey("Swords Dance")||
      a.executed_moves?.p2?.id!==moveKey("Swords Dance")||
      a.executed_moves?.p1?.priority!==0||a.executed_moves?.p2?.priority!==0||
      !Number.isInteger(a.executed_seed)||a.executed_seed<1||a.executed_seed>48||
      a.preempted_paralysis!==a.executed_seed-1||
      a.synthetic_initial_duration!==(ref.weather?5:null)||
      a.provenance!=="SYNTHETIC_PRETURN_WEATHER_STATUS_BOOST_NOT_DEVICE")
      throw Error("weather Gate A reviewed case changed: "+ref.id);
 }
 if(r.counts?.SOURCE_SPEC_MATCH!==12||
    r.counts?.SOURCE_SPEC_MISMATCH!==0||
    r.counts?.SOURCE_UNKNOWN_MODIFIER_ORDER!==1)
   throw Error("weather Gate A source count drift");
 return {matches:12,rounding_unknown:1,source_agreed_unexecuted:1,same_weather_reapply_unexecuted:1,
   executed_lifecycle_cases:1,scope:"PINNED_SYNTHETIC_WEATHER_NOT_GAME"};
}
module.exports={checkWeatherEvidence};
