"use strict";
// Champions secondary sources only, not device evidence. This is separate from Showdown.
const LEDGER=require("./champions_damage_rounding_secondary_ledger.json");
const {calculate,halfDownRatio}=require("./wiki_damage_reference.cjs");
const {referenceInput,LEDGER:OLD,WITNESS_ID,REVIEWED_PINNED_DIVERGENCE}=
 require("./champions_damage_modifier_probe.cjs");
const SOURCE_CLAIMS=Object.freeze([
 {id:"combined_final",url:"https://pkmn-cp-dataportal.com/battle/damage/damage-order/",
  rule_scope:"BURN_AND_REFLECT_SAME_FINAL_MODIFIER_STEP",
  claim:"Combine burn and Reflect before one half-down round at final modifier stage",
  confidence:"SECONDARY_DESCRIPTION_ONLY"},
 {id:"separate_steps",url:"https://damekei.com/formula",
  rule_scope:"BURN_THEN_WALL_SEPARATE_STEPS",
  claim:"Burn and Reflect are listed as successive stages with floor applied at each step",
  confidence:"SECONDARY_DESCRIPTION_ONLY_NOT_FULL_FORMULA_NUMERIC_MATCH"}
]);
const SCOPE={attacker:"Garchomp",defender:"Blastoise",attack_move:"Earthquake",
 burn:true,reflect:true,critical:false,weather:"",item:"",rolls:[85,100],
 pre_final_basis:"EXISTING_CHAMPIONS_PORTAL_ARITHMETIC_NOT_DEVICE",
 witness_rolls:[86,90,91,98],combined_expected:[22,23,23,25],
 sequential_expected:[21,22,22,24]};
const UNKNOWN=[
 "Actual Champions real-game rounding order or observed HP loss",
 "Full damekei damage formula equivalence for this fixture, including other rounding stages",
 "Crits, alternate abilities, screens expiring, accuracy and other turn effects outside reviewed synthetic fixture",
 "Turn-level damage random seed distribution and probability",
 "Any safety/KO guarantee based on 1-point discrepancy"
];
const CLASSIFICATION="SECONDARY_METHODS_DISAGREE_ON_THIS_INTERMEDIATE_STEP;PINNED_GETDAMAGE_MATCHES_SEQUENTIAL_FOR_FOUR_WITNESSES;GAME_BEHAVIOR_UNKNOWN";
const DEPENDENCY="Different guides may share older game arithmetic or implementation assumptions; independence of original game evidence not established";
function checkLedger(l=LEDGER){
 if(!l||l.schema!=="phase0-champions-damage-rounding-secondary-ledger-v1"||
   l.regulation!=="M-C"||l.checked_at!=="2026-11-02"||
   l.evidence!=="TWO_CHAMPIONS_SECONDARY_DESCRIPTIONS_NOT_DEVICE"||
   l.device_observations!==0||l.private_golden_read!==false||
   l.live_use_approved!==false||
   JSON.stringify(l.source_claims)!==JSON.stringify(SOURCE_CLAIMS)||
   JSON.stringify(l.specific_scope)!==JSON.stringify(SCOPE)||
   l.classification!==CLASSIFICATION||
   l.source_dependency_warning!==DEPENDENCY||
   JSON.stringify(l.unverified)!==JSON.stringify(UNKNOWN))
   throw Error("rounding secondary source contract drift");
 return true;
}
function secondaryComparison(){
 checkLedger();
 const c=OLD.scenarios.find(x=>x.id===WITNESS_ID);
 if(!c||!c.burn||!c.reflect||c.critical||c.other.join("/")!=="1/4")
   throw Error("reviewed witness setup drift");
 const neutral=calculate(referenceInput(c,[1,1])).rolls;
 const oneQuarter=calculate(referenceInput(c)).rolls;
 const rows=neutral.map((before,i)=>{
   const combined=Number(halfDownRatio(BigInt(before),[1n,4n]));
   const sequential=Math.floor(Math.floor(before/2)/2);
   if(oneQuarter[i]!==combined)throw Error("combined source arithmetic drift");
   return {roll_pct:85+i,pre_final:before,combined,sequential,
     discriminating:combined!==sequential};
 });
 const witnesses=rows.filter(x=>x.discriminating);
 if(JSON.stringify(witnesses.map(x=>x.roll_pct))!==
    JSON.stringify(SCOPE.witness_rolls)||
    JSON.stringify(witnesses.map(x=>x.combined))!==
    JSON.stringify(SCOPE.combined_expected)||
    JSON.stringify(witnesses.map(x=>x.sequential))!==
    JSON.stringify(SCOPE.sequential_expected)||
    JSON.stringify(witnesses.map(x=>({
      roll_pct:x.roll_pct,pre_final_modifier:x.pre_final,
      combined_rounding:x.combined,split_rounding:x.sequential})))!==
    JSON.stringify(REVIEWED_PINNED_DIVERGENCE))
    throw Error("reviewed secondary rounding witnesses changed");
 return {schema:"phase0-champions-damage-secondary-comparison-v1",
   source_ids:SOURCE_CLAIMS.map(s=>s.id),source_evidence:LEDGER.evidence,
   row_count:rows.length,witness_count:witnesses.length,
   classification:CLASSIFICATION,
   caveat:"Different source statements concern only the final multiplier stage; the complete damekei formula is not reproduced",
   game_verified:false,rows,witnesses};
}
module.exports={LEDGER,SOURCE_CLAIMS,SCOPE,UNKNOWN,checkLedger,secondaryComparison};
