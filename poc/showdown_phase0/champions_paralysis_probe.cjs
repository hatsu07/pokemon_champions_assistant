"use strict";
// Phase 0 only: secondary Champions spec vs executed synthetic pinned Showdown turns.
// Do not load private Golden or use in LIVE.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {LEDGER,checkLedger,reference}=require("./champions_paralysis_reference.cjs");
const {makeFixture,chooseIndex,checkMovePriorities,
 validateSelectedMove,observeMoves,PIN,FORMAT}=require("./champions_turn_order_probe.cjs");
const {teamsForScenario,validateChampionsRegisteredTeam,allowedChampionsSets}=
 require("./wiki_damage_compare.cjs");
const NUM_SEEDS=48;
function checkPinnedRateSource(root){
 const src=fs.readFileSync(path.join(root,"data/mods/champions/conditions.ts"),"utf8");
 const inherited=fs.readFileSync(path.join(root,"data/conditions.ts"),"utf8");
 const own=src.match(/\bpar:\s*\{([\s\S]*?)\n\t\},/);
 const base=inherited.match(/\bpar:\s*\{([\s\S]*?)\n\t\},/);
 if(!own||!base||!own[1].includes("inherit: true")||
    !/this\.randomChance\(1,\s*8\)/.test(own[1])||
    !base[1].includes("onModifySpe")||
    !/Math\.floor\(spe\s*\*\s*50\s*\/\s*100\)/.test(base[1]))
   throw Error("pinned Champions paralysis 1/8 or inherited Speed half source changed");
 return {pinned_full_paralysis_chance:"1/8",
   pinned_speed_half_floor:true,
   evidence:"PINNED_SOURCE_IMPLEMENTATION_NOT_GAME_OR_STATISTICAL_VALIDATION"};
}
function setupCase(c,referenceRow,ctx,seed){
 const fixture=makeFixture(c);
 fixture.attacker={...fixture.attacker,item:c.g_scarf?"Choice Scarf":""};
 const registered=teamsForScenario(fixture);
 for(const team of registered){
   validateChampionsRegisteredTeam(team,ctx.dex,ctx.allowed);
   const problems=ctx.validator.validateTeam(structuredClone(team));
   if(problems?.length)throw Error("invalid six-member M-C registered team: "+problems.join(" | "));
 }
 const b=new ctx.Battle({formatid:FORMAT,seed:[1,2,3,seed],strictChoices:true,
   p1:{name:"Paralysis speed A",team:registered[0]},
   p2:{name:"Paralysis speed B",team:registered[1]}});
 if(b.requestState==="teampreview")b.makeChoices("team 123","team 123");
 const p1=b.p1.active[0],p2=b.p2.active[0];
 if(b.requestState!=="move"||b.p1.pokemon.length!==3||b.p2.pokemon.length!==3||
    p1.species.id!=="garchomp"||p2.species.id!=="metagross"||
    p1.baseStoredStats.spe!==referenceRow.p1_base_speed||
    p2.baseStoredStats.spe!==referenceRow.p2_base_speed||
    p1.item!==(c.g_scarf?"choicescarf":"")||
    p2.item!==(c.m_scarf?"choicescarf":""))
   throw Error("Paralysis selected fixture precondition drift "+c.id);
 if(c.g_par&&!p1.setStatus("par"))throw Error("engine did not apply Garchomp paralysis");
 if(c.m_par&&!p2.setStatus("par"))throw Error("engine did not apply Metagross paralysis");
 if(c.m_stage===1)p2.boosts.spe=1; // Synthetic preturn boost, NOT move setup.
 if(c.trick_room&&!b.field.addPseudoWeather("trickroom",p2))
   throw Error("synthetic active Trick Room precondition failed");
 if(p1.status!==(c.g_par?"par":"")||p2.status!==(c.m_par?"par":"")||
    p2.boosts.spe!==c.m_stage||
    !!b.field.pseudoWeather.trickroom!==c.trick_room)
   throw Error("paralysis/boost/Trick Room precondition mismatch");
 const actualSpeeds={p1:p1.getStat("spe"),p2:p2.getStat("spe")};
 if((referenceRow.p1_effective_speed!==null&&
      actualSpeeds.p1!==referenceRow.p1_effective_speed)||
    (referenceRow.p2_effective_speed!==null&&
      actualSpeeds.p2!==referenceRow.p2_effective_speed))
   throw Error("pinned paralysis effective Speed vs independently invariant source mismatch "+
     c.id+" "+JSON.stringify({actualSpeeds,referenceRow}));
 return {b,p1,p2,actualSpeeds};
}
function execute(c,ref,ctx,seed){
 const {b,p1,p2,actualSpeeds}=setupCase(c,ref,ctx,seed);
 try{
   const choice1=chooseIndex(c.p1,"p1"),choice2=chooseIndex(c.p2,"p2");
   const moves={p1:validateSelectedMove(p1,choice1,c.p1,ref.p1_priority,ctx.dex),
     p2:validateSelectedMove(p2,choice2,c.p2,ref.p2_priority,ctx.dex)};
   const at=b.log.length;
   b.makeChoices(choice1,choice2);
   const events=b.log.slice(at);
   const prevented=events.filter(x=>/^\|cant\|p[12]a:/.test(x)&&x.includes("|par"));
   if(prevented.length){
     // Full paralysis can prevent a move without invalidating its planned priority.
     if(prevented.length>2)throw Error("unexpected number of paralysis interruptions");
     return {seed,kind:"FULL_PARALYSIS_INTERRUPTED",events:events.filter(x=>
       x.startsWith("|move|")||x.startsWith("|cant|")),actualSpeeds};
   }
   const observed=observeMoves(events,moves,ctx.dex);
   return {seed,kind:"BOTH_MOVES_EXECUTED",observed,actualSpeeds};
 }finally{b.destroy?.();}
}
function run(){
 checkLedger();
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT required");
 const actualSha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(actualSha!==PIN)throw Error("unpinned Showdown SHA");
 const source=checkPinnedRateSource(root);
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT);
 if(dex.currentMod!=="champions")throw Error("not in pinned Champions mod");
 const ctx={Battle,dex,validator:TeamValidator.get(FORMAT),allowed:allowedChampionsSets(dex)};
 const checkedPriority=checkMovePriorities(dex,ctx.allowed);
 const results=[];
 for(const c of LEDGER.cases){
   const ref=reference(c);
   let completed=null,blocked=0;
   for(let seed=1;seed<=NUM_SEEDS;seed++){
     const r=execute(c,ref,ctx,seed);
     if(r.kind==="FULL_PARALYSIS_INTERRUPTED"){blocked++;continue}
     completed=r;break;
   }
   if(!completed)throw Error("no executable independent order turn in seeded envelope: "+c.id);
   const uncertain=ref.expected_first==="UNREVIEWED_ROUNDING";
   const resultStatus=uncertain?"SOURCE_UNKNOWN_MODIFIER_ORDER":
     completed.observed.first===ref.expected_first?"SOURCE_SPEC_MATCH":"SOURCE_SPEC_MISMATCH";
   results.push({id:c.id,status:resultStatus,source_expectation:ref.expected_first,
     p1_base_speed:ref.p1_base_speed,p2_base_speed:ref.p2_base_speed,
     p1_effective_speed:ref.p1_effective_speed,p2_effective_speed:ref.p2_effective_speed,
     p1_possible_speeds:ref.p1_possible_speeds,p2_possible_speeds:ref.p2_possible_speeds,
     p1_priority:ref.p1_priority,p2_priority:ref.p2_priority,
     pinned_effective_speed:completed.actualSpeeds,
     observed_first:completed.observed.first,
     observed_sequence:completed.observed.sequence,
     executed_moves:completed.observed.executed_moves,
     executed_seed:completed.seed,full_paralysis_preempted_attempts:blocked,
     provenance:"SYNTHETIC_PRETURN_STATUS_BOOST_TRICKROOM_NOT_DEVICE"});
 }
 // This witnesses the engine's paralyzed/move-failed path; no empirical rate inference.
 const sampleCase=LEDGER.cases[0],sampleRef=reference(sampleCase);
 let interrupted=0,uninterrupted=0;
 for(let seed=1;seed<=NUM_SEEDS;seed++){
   const result=execute(sampleCase,sampleRef,ctx,seed);
   if(result.kind==="FULL_PARALYSIS_INTERRUPTED")interrupted++;
   else uninterrupted++;
 }
 const counts={SOURCE_SPEC_MATCH:results.filter(x=>x.status==="SOURCE_SPEC_MATCH").length,
   SOURCE_SPEC_MISMATCH:results.filter(x=>x.status==="SOURCE_SPEC_MISMATCH").length,
   SOURCE_UNKNOWN_MODIFIER_ORDER:results.filter(x=>x.status==="SOURCE_UNKNOWN_MODIFIER_ORDER").length};
 return {schema:"phase0-champions-paralysis-order-diagnostic-v1",pinned_sha:PIN,
   format:FORMAT,regulation:"M-C",evidence:LEDGER.evidence,
   checked_move_priorities:checkedPriority,source_full_paralysis_rate:"1/8",
   pinned_implementation:source,full_paralysis_sample:
     {synthetic_turns:NUM_SEEDS,interrupted,uninterrupted,
      empirical_probability_verified:false,scope:"PINNED_SEEDS_NOT_DEVICE_OR_PROBABILITY_PROOF"},
   registered_six_selected_three:true,case_count:results.length,counts,results,
   game_verified_turn_count:0,private_golden_read:false,live_use_approved:false,
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",unverified:LEDGER.not_verified};
}
if(require.main===module){
 try{const r=run();process.stdout.write(JSON.stringify(r)+"\n");
   if(r.counts.SOURCE_SPEC_MISMATCH)process.exitCode=1;
 }catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}
}
module.exports={run,checkPinnedRateSource,setupCase,execute,NUM_SEEDS};
