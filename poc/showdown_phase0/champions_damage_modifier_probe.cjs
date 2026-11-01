"use strict";
// Champions-specific secondary-source modifier arithmetic vs fixed pinned Showdown.
// This is NOT evidence of real Pokémon Champions device parity.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {calculate,halfDownRatio}=require("./wiki_damage_reference.cjs");
const {allowedChampionsSets,teamsForScenario,validateChampionsScenario,
  SCENARIOS}=require("./wiki_damage_compare.cjs");
const LEDGER=require("./champions_damage_modifier_source_ledger.json");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT="gen9championsbssregmc";
const WITNESS_ID="neutral-type-burn-and-reflect";
const REVIEWED_SOURCE_URLS=Object.freeze({
 damage_order:"https://pkmn-cp-dataportal.com/battle/damage/damage-order/",
 critical:"https://pkmn-cp-dataportal.com/battle/damage/critical-hit/",
 burn:"https://pkmn-cp-dataportal.com/status/burn/"
});
// An ID must not silently change its actual conditions, even when its
// final numeric otherMultiplier happens to be identical (critical vs wall).
const REVIEWED_CONTRACTS=Object.freeze([
 ["neutral",false,false,false,"1/1",["damage_order"]],
 ["critical",false,false,true,"1/1",["damage_order","critical"]],
 ["burn",true,false,false,"1/2",["damage_order","burn"]],
 ["reflect",false,true,false,"1/2",["damage_order"]],
 ["burn-and-reflect",true,true,false,"1/4",["damage_order","burn"]],
 ["critical-ignores-reflect",false,true,true,"1/1",["critical"]],
 ["critical-preserves-burn",true,false,true,"1/2",["critical","burn"]],
 ["critical-ignores-reflect-preserves-burn",true,true,true,"1/2",["critical","burn"]],
 [WITNESS_ID,true,true,false,"1/4",["damage_order","burn"]]
].map(([id,burn,reflect,critical,other,source_ids])=>
 Object.freeze({id,burn,reflect,critical,other,source_ids:Object.freeze(source_ids)})));
const REVIEWED_IDS=REVIEWED_CONTRACTS.map(r=>r.id);
function validateSource(input=LEDGER){
 if(input.schema!=="phase0-champions-damage-modifier-source-v1"||
    input.regulation!=="M-C"||input.private_golden_read!==false||
    input.game_verified_check_count!==0||input.live_use_approved!==false||
    !Array.isArray(input.sources)||input.sources.length!==3||
    Object.keys(REVIEWED_SOURCE_URLS).some((id,index)=>{
      const s=input.sources[index];
      return s?.id!==id||s?.url!==REVIEWED_SOURCE_URLS[id]||
        s?.source_type!=="SECONDARY_COMMUNITY";
    })||
    JSON.stringify(input.scenarios?.map(s=>s.id))!==JSON.stringify(REVIEWED_IDS))
   throw Error("unreviewed damage source or scenario manifest");
 for(let i=0;i<input.scenarios.length;i++){
   const c=input.scenarios[i],expected=REVIEWED_CONTRACTS[i];
   if(c.burn!==expected.burn||c.reflect!==expected.reflect||
      c.critical!==expected.critical||
      !Array.isArray(c.other)||c.other.length!==2||
      c.other.join("/")!==expected.other||
      JSON.stringify(c.source_ids)!==JSON.stringify(expected.source_ids)||
      (c.id===WITNESS_ID ?
         (c.fixture!=="DERIVED_NEUTRAL_GROUND_BLASTOISE"||
          c.numeric_basis!=="INDEPENDENT_DAMAGE_FORMULA_NOT_PUBLISHED_NUMERIC_EXAMPLE"||
          c.required_discriminating_witness!==true) :
         Object.hasOwn(c,"fixture")))
     throw Error("unreviewed damage modifier contract: "+c.id);
   const denom=2**(Number(c.burn)+Number(c.reflect&&!c.critical));
   if(c.other[0]!==1||c.other[1]!==denom)
     throw Error("burn/reflect/critical source rule disagreement: "+c.id);
 }
 return true;
}
function referenceInput(c,overrideOther=null){
 const f=LEDGER.fixture;
 if(f.attacker!=="Garchomp"||f.defender!=="Metagross"||
    f.move!=="Earthquake"||f.atk!==182||f.def!==150||
    f.basePower!==100||f.stab.join("/")!=="3/2"||
    f.effectiveness.join("/")!=="2/1")
   throw Error("unreviewed source fixture drift");
 const special=c.id===WITNESS_ID;
 return {attack:f.atk,defense:special?120:f.def,
   basePower:f.basePower,powerMultiplier:[1,1],weatherMultiplier:[1,1],
   critical:c.critical,stabMultiplier:f.stab,
   typeMultiplier:special?[1,1]:f.effectiveness,
   otherMultiplier:overrideOther||c.other};
}
function oddRoundingWitnesses(c){
 if(c.id!==WITNESS_ID)throw Error("odd-rounding witness only for reviewed neutral case");
 const preFinal=calculate(referenceInput(c,[1,1])).rolls;
 const final=calculate(referenceInput(c)).rolls;
 const witnesses=preFinal.flatMap((value,index)=>{
   const combined=Number(halfDownRatio(BigInt(value),[1n,4n]));
   const split=Number(halfDownRatio(
     halfDownRatio(BigInt(value),[1n,2n]),[1n,2n]));
   return combined===split?[]:[{roll_pct:85+index,pre_final_modifier:value,
     combined_rounding:combined,split_rounding:split,
     reference_damage:final[index]}];
 });
 if(!witnesses.length||witnesses.some(w=>w.pre_final_modifier%2===0||
    w.combined_rounding!==w.reference_damage||
    w.combined_rounding===w.split_rounding))
   throw Error("P1: no discriminating odd pre-final-damage witness");
 return witnesses;
}
// Independently captured at pinned SHA in a legal neutral-effectiveness Battle.
// NOT a Pokémon Champions device observation. Deviations must be reviewed,
// not silently upgraded to matching arithmetic.
const REVIEWED_PINNED_DIVERGENCE=Object.freeze([
 {roll_pct:86,pre_final_modifier:87,combined_rounding:22,split_rounding:21},
 {roll_pct:90,pre_final_modifier:91,combined_rounding:23,split_rounding:22},
 {roll_pct:91,pre_final_modifier:91,combined_rounding:23,split_rounding:22},
 {roll_pct:98,pre_final_modifier:99,combined_rounding:25,split_rounding:24}
]);
function verifyKnownDiscrepancy(witnesses,referenceRolls,pinnedRolls){
 const shape=w=>({roll_pct:w.roll_pct,pre_final_modifier:w.pre_final_modifier,
   combined_rounding:w.combined_rounding,split_rounding:w.split_rounding});
 if(JSON.stringify(witnesses.map(shape))!==JSON.stringify(REVIEWED_PINNED_DIVERGENCE))
   throw Error("changed odd rounding witness baseline requires review");
 const diffs=compareRolls(referenceRolls,pinnedRolls).differences;
 if(diffs.length!==REVIEWED_PINNED_DIVERGENCE.length||
    REVIEWED_PINNED_DIVERGENCE.some((v,i)=>
      diffs[i].roll_pct!==v.roll_pct||
      diffs[i].reference!==v.combined_rounding||
      diffs[i].pinned_showdown!==v.split_rounding||
      pinnedRolls[v.roll_pct-85]!==v.split_rounding))
   throw Error("unreviewed damage mismatch outside exact pinned split-rounding baseline");
 return {status:"REVIEWED_SECONDARY_SOURCE_DIVERGENCE",difference_count:diffs.length};
}
function referenceFor(c){
 validateSource();
 const input=referenceInput(c);
 const result=calculate(input);
 if(result.status!=="WIKI_ARITHMETIC_ONLY"||result.rolls.length!==16)
   throw Error("independent damage source returned unexpected status");
 if(Object.hasOwn(c,"published_min")&&result.minimum!==c.published_min)
   throw Error("published minimum changed: "+c.id);
 if(Object.hasOwn(c,"published_max")&&result.maximum!==c.published_max)
   throw Error("published maximum changed: "+c.id);
 return result;
}
function compareRolls(refRolls,actualRolls){
 if(!Array.isArray(refRolls)||!Array.isArray(actualRolls)||
    refRolls.length!==16||actualRolls.length!==16||
    [...refRolls,...actualRolls].some(n=>!Number.isSafeInteger(n)||n<0))
   throw Error("unreadable 16-damage-roll comparison");
 const differences=refRolls.flatMap((v,i)=>v===actualRolls[i]?[]:
   [{roll_pct:85+i,reference:v,pinned_showdown:actualRolls[i]}]);
 return {status:differences.length?"WIKI_SPEC_MISMATCH":"WIKI_SPEC_MATCH",differences};
}
function main(){
 validateSource();
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT required");
 const sha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(sha!==PIN)throw Error("unpinned Showdown: "+sha);
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT);
 if(dex.currentMod!=="champions")throw Error("pinned mod changed");
 const allowed=allowedChampionsSets(dex);
 const fixture=SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
 if(!fixture)throw Error("canonical wiki fixture missing");
 const results=[];
 for(const variant of LEDGER.scenarios){
   const ref=referenceFor(variant);
   // Existing legal Blastoise bench member becomes p2 lead; displaced
   // Metagross becomes bench. No new move/ability/species is guessed.
   let caseFixture=fixture;
   if(variant.id===WITNESS_ID){
     if(fixture.p2bench[2]?.species!=="Blastoise"||
        fixture.defender.species!=="Metagross")
       throw Error("unreviewed legal Blastoise bench placement");
     const bench=[...fixture.p2bench];
     bench[2]=fixture.defender;
     caseFixture={...fixture,defender:fixture.p2bench[2],p2bench:bench};
   }
   const teams=validateChampionsScenario(caseFixture,dex,allowed);
   const validator=TeamValidator.get(FORMAT);
   for(const team of teams){
     const problems=validator.validateTeam(structuredClone(team));
     if(problems?.length)throw Error("six-member M-C registration rejected: "+problems.join(" | "));
   }
   const battle=new Battle({formatid:FORMAT,seed:[1,2,3,4],strictChoices:true,
     p1:{name:"Pinned modifier reference",team:teams[0]},
     p2:{name:"Pinned modifier defense",team:teams[1]}});
   try{
     if(battle.requestState==="teampreview")
       battle.makeChoices("team 123","team 123");
     const attacker=battle.p1.active[0],defender=battle.p2.active[0];
     const odd=variant.id===WITNESS_ID;
     if(attacker.species.id!=="garchomp"||
        defender.species.id!==(odd?"blastoise":"metagross")||
        attacker.baseStoredStats.atk!==LEDGER.fixture.atk||
        defender.baseStoredStats.def!==(odd?120:LEDGER.fixture.def)||
        attacker.ability!=="roughskin"||
        defender.ability!==(odd?"torrent":"clearbody")||
        (odd&&defender.types.join("/")!=="Water"))
       throw Error("reviewed legal damage stat/ability/type precondition drift");
     const move=dex.moves.get("Earthquake");
     if(!move.exists||move.category!=="Physical"||move.basePower!==100||
        move.type!=="Ground"||move.id!=="earthquake")
       throw Error("Champions Earthquake precondition drift");
     if(variant.burn){
       if(!attacker.setStatus("brn",defender)||
          attacker.status!=="brn")throw Error("burn precondition rejected");
     }
     if(variant.reflect){
       if(!battle.p2.addSideCondition("reflect",defender)||
          !battle.p2.getSideCondition("reflect"))
         throw Error("Reflect precondition rejected");
     }
     const rolls=[];
     for(let pct=85;pct<=100;pct++){
       battle.randomizer=base=>battle.trunc(battle.trunc(base*pct)/100);
       const active=battle.dex.getActiveMove(move.id);
       active.willCrit=variant.critical;
       const damage=battle.actions.getDamage(attacker,defender,active,true);
       if(!Number.isSafeInteger(damage))throw Error("noninteger simulated damage");
       rolls.push(damage);
     }
     const result=compareRolls(ref.rolls,rolls);
     const witnesses=variant.id===WITNESS_ID?oddRoundingWitnesses(variant):[];
     const divergence=odd?
       verifyKnownDiscrepancy(witnesses,ref.rolls,rolls):null;
     if(!odd&&result.status!=="WIKI_SPEC_MATCH")
       throw Error("unreviewed source arithmetic divergence outside odd witness case: "+variant.id);
     if(odd&&result.status!=="WIKI_SPEC_MISMATCH")
       throw Error("odd witness source discrepancy disappeared without review");
     results.push({id:variant.id,source_ids:variant.source_ids,
       defender:odd?"Blastoise":"Metagross",
       source_numeric_basis:odd?"DERIVED_FORMULA_ONLY":"PUBLISHED_FIXTURE_AND_DERIVED_MODIFIERS",
       odd_rounding_witnesses:witnesses.map(w=>({...w,
         pinned_damage:rolls[w.roll_pct-85]})),
       reviewed_source_discrepancy:divergence,
       conditions:{burn:variant.burn,reflect:variant.reflect,critical:variant.critical},
       reference_range:[ref.minimum,ref.maximum],
       pinned_range:[Math.min(...rolls),Math.max(...rolls)],
       status:result.status,difference_count:result.differences.length,
       first_differences:result.differences.slice(0,5)});
   }finally{battle.destroy?.();}
 }
 return {schema:"phase0-champions-damage-modifier-diagnostic-v1",
   pinned_sha:PIN,format:FORMAT,regulation:"M-C",
   evidence:"CHAMPIONS_SECONDARY_SOURCE_ARITHMETIC_NOT_DEVICE_GOLDEN",
   scope:LEDGER.scope,source_ids:LEDGER.sources.map(s=>s.id),
   game_verified_check_count:0,private_golden_read:false,
   executed_turns:0,live_use_approved:false,gate_A:"UNKNOWN",
   gate_B:"UNSUPPORTED",adoption:"NO_GO",
   scenario_count:results.length,individual_roll_comparisons:results.length*16,
   odd_rounding_witness_count:results.reduce((n,r)=>n+r.odd_rounding_witnesses.length,0),
   odd_rounding_witness_verified_in_pinned_getDamage:
     results.some(r=>r.id===WITNESS_ID&&r.odd_rounding_witnesses.length>0&&
       r.reviewed_source_discrepancy?.difference_count===4&&
       r.status==="WIKI_SPEC_MISMATCH"),
   source_divergence_count:results.filter(r=>r.status==="WIKI_SPEC_MISMATCH").length,
   reviewed_source_divergence:"PINNED_SPLIT_ROUNDING_VS_SECONDARY_COMBINED_MODIFIER",
   unknown_source_divergences_accepted:false,
   counts:{WIKI_SPEC_MATCH:results.filter(r=>r.status==="WIKI_SPEC_MATCH").length,
     WIKI_SPEC_MISMATCH:results.filter(r=>r.status==="WIKI_SPEC_MISMATCH").length},
   unverified:LEDGER.not_verified,results};
}
if(require.main===module){
 try{
   const result=main();
   process.stdout.write(JSON.stringify(result)+"\n");
   // This exact pinned discrepancy is an audited finding, not a false PASS.
   // Any unreviewed difference throws in main() and exits nonzero.
 }catch(err){
   process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:err.message})+"\n");
   process.exitCode=2;
 }
}
module.exports={main,compareRolls,referenceFor,validateSource,referenceInput,
 oddRoundingWitnesses,verifyKnownDiscrepancy,REVIEWED_PINNED_DIVERGENCE,
 REVIEWED_IDS,REVIEWED_CONTRACTS,REVIEWED_SOURCE_URLS,WITNESS_ID,LEDGER};
