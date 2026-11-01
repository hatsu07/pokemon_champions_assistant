"use strict";
// Champions-specific secondary-source modifier arithmetic vs fixed pinned Showdown.
// This is NOT evidence of real Pokémon Champions device parity.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {calculate}=require("./wiki_damage_reference.cjs");
const {allowedChampionsSets,teamsForScenario,validateChampionsScenario,
  SCENARIOS}=require("./wiki_damage_compare.cjs");
const LEDGER=require("./champions_damage_modifier_source_ledger.json");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT="gen9championsbssregmc";
const REVIEWED_IDS=[
 "neutral","critical","burn","reflect","burn-and-reflect",
 "critical-ignores-reflect","critical-preserves-burn",
 "critical-ignores-reflect-preserves-burn"
];
const RATIO_REVIEWED={
 neutral:"1/1",critical:"1/1",burn:"1/2",reflect:"1/2",
 "burn-and-reflect":"1/4","critical-ignores-reflect":"1/1",
 "critical-preserves-burn":"1/2",
 "critical-ignores-reflect-preserves-burn":"1/2"
};
function validateSource(){
 if(LEDGER.schema!=="phase0-champions-damage-modifier-source-v1"||
    LEDGER.regulation!=="M-C"||LEDGER.private_golden_read!==false||
    LEDGER.game_verified_check_count!==0||LEDGER.live_use_approved!==false||
    LEDGER.sources.length!==3||
    LEDGER.sources.some(s=>s.source_type!=="SECONDARY_COMMUNITY"||
      !s.url.startsWith("https://"))||
    JSON.stringify(LEDGER.scenarios.map(s=>s.id))!==JSON.stringify(REVIEWED_IDS))
   throw Error("unreviewed damage source or scenario manifest");
 for(const c of LEDGER.scenarios){
   if(typeof c.critical!=="boolean"||typeof c.burn!=="boolean"||
      typeof c.reflect!=="boolean"||
      c.other.join("/")!==RATIO_REVIEWED[c.id]||
      !Array.isArray(c.source_ids)||!c.source_ids.length||
      c.source_ids.some(id=>!LEDGER.sources.some(s=>s.id===id))||
      c.other.length!==2)
     throw Error("unreviewed damage modifier contract: "+c.id);
   const expectedOtherDenom=2**(Number(c.burn)+Number(c.reflect&&!c.critical));
   if(c.other[0]!==1||c.other[1]!==expectedOtherDenom)
     throw Error("burn/reflect/critical source rule disagreement: "+c.id);
 }
 return true;
}
function referenceFor(c){
 validateSource();
 const f=LEDGER.fixture;
 if(f.attacker!=="Garchomp"||f.defender!=="Metagross"||
    f.move!=="Earthquake"||f.atk!==182||f.def!==150||
    f.basePower!==100||f.stab.join("/")!=="3/2"||
    f.effectiveness.join("/")!=="2/1")
   throw Error("unreviewed source fixture drift");
 const input={attack:f.atk,defense:f.def,basePower:f.basePower,
   powerMultiplier:[1,1],weatherMultiplier:[1,1],
   critical:c.critical,stabMultiplier:f.stab,
   typeMultiplier:f.effectiveness,otherMultiplier:c.other};
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
   const teams=validateChampionsScenario(fixture,dex,allowed);
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
     if(attacker.species.id!=="garchomp"||defender.species.id!=="metagross"||
        attacker.baseStoredStats.atk!==LEDGER.fixture.atk||
        defender.baseStoredStats.def!==LEDGER.fixture.def||
        attacker.ability!=="roughskin"||defender.ability!=="clearbody")
       throw Error("reviewed 2-species stat/ability precondition drift");
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
     results.push({id:variant.id,source_ids:variant.source_ids,
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
   counts:{WIKI_SPEC_MATCH:results.filter(r=>r.status==="WIKI_SPEC_MATCH").length,
     WIKI_SPEC_MISMATCH:results.filter(r=>r.status==="WIKI_SPEC_MISMATCH").length},
   unverified:LEDGER.not_verified,results};
}
if(require.main===module){
 try{
   const result=main();
   process.stdout.write(JSON.stringify(result)+"\n");
   if(result.counts.WIKI_SPEC_MISMATCH)process.exitCode=1;
 }catch(err){
   process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:err.message})+"\n");
   process.exitCode=2;
 }
}
module.exports={main,compareRolls,referenceFor,validateSource,REVIEWED_IDS,LEDGER};
