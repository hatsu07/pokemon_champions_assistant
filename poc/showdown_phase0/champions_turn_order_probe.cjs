"use strict";
// Phase 0 only: independently documented ordering versus pinned Showdown executed turns.
// Not an official/device ordering proof, never import into LIVE.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {rows}=require("./audit.cjs");
const {SCENARIOS,teamsForScenario,validateChampionsRegisteredTeam,
  allowedChampionsSets}=require("./wiki_damage_compare.cjs");
const {LEDGER,REVIEWED_MOVES,validateLedger,referenceCase}=require("./champions_turn_order_reference.cjs");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT="gen9championsbssregmc";
function checkMovePriorities(dex,allowed){
 const dir=process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,"masters");
 const moveRows=rows(fs.readFileSync(path.join(dir,"Pokemon_Champions_MC_move_master.csv"),"utf8"));
 const seen=new Set();
 for(const [species,name,expectedPriority] of REVIEWED_MOVES){
   const move=dex.moves.get(name);
   const master=moveRows.filter(x=>x.MoveKey===move.id);
   if(master.length!==1||!allowed.availableMoves.has(move.id)||
      Number(master[0]["優先度"])!==expectedPriority||
      !move.exists||move.priority!==expectedPriority)
     throw Error("fixed Champions move priority drift: "+name);
   const key=species+"/"+move.id;
   if(seen.has(key))throw Error("duplicate reviewed move");
   seen.add(key);
 }
 return seen.size;
}
function chooseIndex(name,side){
 const slots=side==="p1"?["Swords Dance","Protect","Earthquake"]:
   ["Iron Defense","Bullet Punch"];
 const i=slots.indexOf(name);
 if(i<0)throw Error("unreviewed move choice "+side+"/"+name);
 return "move "+(i+1);
}
function makeFixture(c){
 const original=SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
 if(!original||original.attacker.species!=="Garchomp"||
    original.defender.species!=="Metagross")throw Error("source base fixture drift");
 return {...original,
  attacker:{...original.attacker,
    evs:{...original.attacker.evs,spe:c.g_ap},
    moves:["Swords Dance","Protect","Earthquake"]},
  defender:{...original.defender,
    evs:{...original.defender.evs,spe:c.m_ap},
    item:c.m_scarf?"Choice Scarf":"",
    moves:["Iron Defense","Bullet Punch"]}
 };
}
function observeMoves(log){
 const events=log.filter(x=>x.startsWith("|move|"));
 const side=(event)=>event.startsWith("|move|p1a:")?"p1":
   event.startsWith("|move|p2a:")?"p2":null;
 const sides=events.map(side);
 if(sides.length!==2||sides.some(x=>x===null)||new Set(sides).size!==2)
   throw Error("executed move order not unambiguously observable: "+JSON.stringify(events));
 return {first:sides[0],sequence:sides,move_events:events};
}
function run(){
 validateLedger();
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT required for fixed offline Showdown diagnostic");
 const sha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(sha!==PIN)throw Error("unpinned Showdown commit "+sha);
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT);
 if(dex.currentMod!=="champions")throw Error("not using pinned Champions mod");
 const allowed=allowedChampionsSets(dex);
 const checkedMoveCount=checkMovePriorities(dex,allowed);
 const validator=TeamValidator.get(FORMAT);
 const results=[];
 for(const c of LEDGER.cases){
   const reference=referenceCase(c);
   const fixture=makeFixture(c);
   const teams=teamsForScenario(fixture);
   for(const team of teams){
     validateChampionsRegisteredTeam(team,dex,allowed);
     const problems=validator.validateTeam(structuredClone(team));
     if(problems?.length)throw Error("M-C six-member TeamValidator rejected "+c.id+": "+problems.join(" | "));
   }
   const b=new Battle({formatid:FORMAT,seed:[1,2,3,4],strictChoices:true,
     p1:{name:"Turn-order source A",team:teams[0]},
     p2:{name:"Turn-order source B",team:teams[1]}});
   try{
     if(b.requestState==="teampreview")b.makeChoices("team 123","team 123");
     const p1=b.p1.active[0],p2=b.p2.active[0];
     if(b.requestState!=="move"||b.p1.pokemon.length!==3||
        b.p2.pokemon.length!==3||
        p1.species.id!=="garchomp"||p2.species.id!=="metagross"||
        p1.baseStoredStats.spe!==reference.p1_base_speed||
        p2.baseStoredStats.spe!==reference.p2_base_speed||
        p2.item!==(c.m_scarf?"choicescarf":""))
        throw Error("reviewed selected speed/team/item preconditions changed: "+c.id);
     if(c.m_stage===1){
       // Synthetic pre-turn state injection only; NOT an executed boost move.
       p2.boosts.spe=1;
     }
     if(c.trick_room){
       // Synthetic active-field precondition, NOT an executed Trick Room setup.
       if(!b.field.addPseudoWeather("trickroom")||!b.field.pseudoWeather.trickroom)
         throw Error("pinned Trick Room precondition unavailable");
     }
     if(p2.boosts.spe!==c.m_stage||
        !!b.field.pseudoWeather.trickroom!==c.trick_room)
       throw Error("synthetic boost or Trick Room precondition drift");
     const at=b.log.length;
     b.makeChoices(chooseIndex(c.p1,"p1"),chooseIndex(c.p2,"p2"));
     const observed=observeMoves(b.log.slice(at));
     const expected=reference.expected_first;
     const status=expected==="UNRESOLVED_TIE"?"INCONCLUSIVE_SPEED_TIE":
       observed.first===expected?"SOURCE_SPEC_MATCH":"SOURCE_SPEC_MISMATCH";
     results.push({id:c.id,status,expected_first:expected,
       observed_first:observed.first,observed_sequence:observed.sequence,
       source_only_preconditions:{synthetic_speed_boost:c.m_stage,
         synthetic_trick_room:c.trick_room,scarf_held:c.m_scarf},
       comparison_scope:expected==="UNRESOLVED_TIE"?
         "TIE_OUTCOME_NOT_A_SINGLE_VALUE_ASSERTION":"EXECUTED_PINNED_TURN_VS_SECONDARY_SOURCE",
       p1_base_speed:reference.p1_base_speed,
       p2_base_speed:reference.p2_base_speed,
       p1_effective_speed:reference.p1_effective_speed,
       p2_effective_speed:reference.p2_effective_speed,
       p1_priority:reference.p1_priority,p2_priority:reference.p2_priority});
   }finally{b.destroy?.();}
 }
 const counts={SOURCE_SPEC_MATCH:results.filter(x=>x.status==="SOURCE_SPEC_MATCH").length,
   SOURCE_SPEC_MISMATCH:results.filter(x=>x.status==="SOURCE_SPEC_MISMATCH").length,
   INCONCLUSIVE_SPEED_TIE:results.filter(x=>x.status==="INCONCLUSIVE_SPEED_TIE").length};
 return {schema:"phase0-champions-turn-order-diagnostic-v1",
   pinned_sha:PIN,format:FORMAT,regulation:"M-C",
   evidence:LEDGER.evidence,source_ids:LEDGER.sources.map(x=>x.id),
   game_verified_turn_count:0,private_golden_read:false,
   pinned_executed_turn_count:results.length,
   registered_six_member_teams:true,selected_three_member_teams:true,
   checked_move_priority_count:checkedMoveCount,
   scenarios:results.length,counts,results,
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
   official_or_device_confirmed:false,live_use_approved:false,
   unverified:LEDGER.not_verified};
}
if(require.main===module){
 try{const r=run();process.stdout.write(JSON.stringify(r)+"\n");
   if(r.counts.SOURCE_SPEC_MISMATCH)process.exitCode=1;
 }catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}
}
module.exports={run,checkMovePriorities,makeFixture,observeMoves,chooseIndex,PIN,FORMAT};
