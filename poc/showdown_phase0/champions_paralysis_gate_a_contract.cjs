"use strict";
// Separate Phase 0 Gate A evidence contract. No game parity inference.
const {LEDGER,reference}=require("./champions_paralysis_reference.cjs");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
function checkParalysisEvidence(r){
 if(r?.schema!=="phase0-champions-paralysis-order-diagnostic-v1"||
    r.pinned_sha!==PIN||r.format!=="gen9championsbssregmc"||r.regulation!=="M-C"||
    r.evidence!=="CHAMPIONS_SECONDARY_SOURCE_PLUS_FIXED_MASTERS_NOT_DEVICE"||
    r.gate_A!=="UNKNOWN"||r.gate_B!=="UNSUPPORTED"||r.adoption!=="NO_GO"||
    r.game_verified_turn_count!==0||r.private_golden_read!==false||
    r.live_use_approved!==false||r.registered_six_selected_three!==true||
    r.checked_move_priorities!==5||r.case_count!==10||r.results?.length!==10||
    r.source_full_paralysis_rate!=="1/8"||
    r.pinned_implementation?.pinned_full_paralysis_chance!=="1/8"||
    r.pinned_implementation?.pinned_speed_half_floor!==true||
    r.full_paralysis_sample?.synthetic_turns!==48||
    r.full_paralysis_sample?.empirical_probability_verified!==false||
    r.full_paralysis_sample.interrupted+r.full_paralysis_sample.uninterrupted!==48)
    throw Error("paralysis Gate A source contract changed");
 for(const [i,c] of LEDGER.cases.entries()){
   const row=r.results[i],ref=reference(c);
   const uncertain=i===9;
   if(!row||row.id!==c.id||
      row.status!==(uncertain?"SOURCE_UNKNOWN_MODIFIER_ORDER":"SOURCE_SPEC_MATCH")||
      row.source_expectation!==ref.expected_first||
      (!uncertain&&row.observed_first!==ref.expected_first)||
      !["p1","p2"].includes(row.observed_first)||
      row.observed_sequence?.length!==2||
      row.observed_sequence[0]!==row.observed_first||
      row.observed_sequence[1]===row.observed_first||
      row.p1_base_speed!==ref.p1_base_speed||
      row.p2_base_speed!==ref.p2_base_speed||
      row.p1_effective_speed!==ref.p1_effective_speed||
      row.p2_effective_speed!==ref.p2_effective_speed||
      JSON.stringify(row.p1_possible_speeds)!==JSON.stringify(ref.p1_possible_speeds)||
      JSON.stringify(row.p2_possible_speeds)!==JSON.stringify(ref.p2_possible_speeds)||
      row.p1_priority!==ref.p1_priority||row.p2_priority!==ref.p2_priority||
      row.executed_moves?.p1?.name!==c.p1||
      row.executed_moves?.p2?.name!==c.p2||
      row.executed_moves?.p1?.priority!==ref.p1_priority||
      row.executed_moves?.p2?.priority!==ref.p2_priority||
      row.pinned_effective_speed?.p1!==ref.p1_effective_speed||
      (!uncertain&&row.pinned_effective_speed?.p2!==ref.p2_effective_speed)||
      (uncertain&&![103,104].includes(row.pinned_effective_speed?.p2))||
      !Number.isInteger(row.executed_seed)||row.executed_seed<1||
      row.executed_seed>48||
      row.full_paralysis_preempted_attempts!==row.executed_seed-1||
      row.provenance!=="SYNTHETIC_PRETURN_STATUS_BOOST_TRICKROOM_NOT_DEVICE")
     throw Error("paralysis Gate A reviewed case changed: "+c.id);
 }
 if(r.counts?.SOURCE_SPEC_MATCH!==9||
    r.counts?.SOURCE_SPEC_MISMATCH!==0||
    r.counts?.SOURCE_UNKNOWN_MODIFIER_ORDER!==1)
   throw Error("paralysis Gate A source counts changed");
 return {matches:9,rounding_unknown:1,scope:"SYNTHETIC_PINNED_TURNS_NOT_DEVICE"};
}
module.exports={checkParalysisEvidence};
