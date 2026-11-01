"use strict";
// Phase 0 synthetic evidence only. No device claims, LIVE use or private Golden.
const fs=require("node:fs"),path=require("node:path"),{execFileSync}=require("node:child_process");
const {LEDGER,CASES,reference,checkLedger,masterCheck}=require("./champions_weather_speed_reference.cjs");
const {SCENARIOS,teamsForScenario,validateChampionsRegisteredTeam,allowedChampionsSets}=
 require("./wiki_damage_compare.cjs");
const {validateSelectedMove,observeMoves,PIN,FORMAT}=require("./champions_turn_order_probe.cjs");
const {paralysisInterruptions}=require("./champions_paralysis_probe.cjs");
function baseFixture(){
 const src=SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
 if(!src||src.attacker.species!=="Garchomp"||src.defender.species!=="Metagross")
   throw Error("pinned weather registered base fixture missing");
 return src;
}
function mon(src,changes){
 return {...src,evs:{...src.evs},moves:[...src.moves],...changes};
}
function weatherFixture(c,r){
 const original=baseFixture(),base=original.attacker;
 const weatherUser=mon(base,{species:r.species,ability:r.ability,
   item:r.scarf?"Choice Scarf":"",moves:["Swords Dance"],
   evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2}});
 const opponent=mon(base,{ability:"Rough Skin",moves:["Swords Dance"],
   evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2}});
 const p1bench=original.p1bench.map(m=>mon(m,{}));
 const p2bench=original.p2bench.map(m=>mon(m,{}));
 if(r.species==="Venusaur"){
   const idx=p1bench.findIndex(x=>x.species==="Venusaur");
   if(idx<0)throw Error("not replaced Venusaur bench duplicate");
   p1bench[idx]=mon(base,{});
 }
 const idx=p2bench.findIndex(x=>x.species==="Garchomp");
 if(idx<0)throw Error("not replaced Garchomp bench duplicate");
 p2bench[idx]=mon(original.defender,{});
 return {attacker:weatherUser,defender:opponent,p1bench,p2bench};
}
function make(ctx,fixture,seed=4){
 const teams=teamsForScenario(fixture);
 for(const t of teams){
   validateChampionsRegisteredTeam(t,ctx.dex,ctx.allowed);
   const errors=ctx.validator.validateTeam(structuredClone(t));
   if(errors?.length)throw Error("invalid reviewed M-C weather team: "+errors.join(" | "));
 }
 const b=new ctx.Battle({formatid:FORMAT,seed:[1,2,3,seed],strictChoices:true,
   p1:{name:"Weather Phase0 A",team:teams[0]},
   p2:{name:"Weather Phase0 B",team:teams[1]}});
 if(b.requestState==="teampreview")b.makeChoices("team 123","team 123");
 if(b.format.mod!=="champions"||b.p1.pokemon.length!==3||
    b.p2.pokemon.length!==3||b.requestState!=="move")
   throw Error("weather M-C 6 registered to selected three precondition drift");
 return b;
}
function runCase(ctx,c){
 const r=reference(c);
 let completed=null,blocked=0;
 for(let seed=1;seed<=48;seed++){
   const b=make(ctx,weatherFixture(c,r),seed);
   try{
     const p1=b.p1.active[0],p2=b.p2.active[0];
     if(p1.species.id!==ctx.dex.species.get(r.species).id||
        p1.ability!==ctx.dex.abilities.get(r.ability).id||
        p2.species.id!=="garchomp"||p1.baseStoredStats.spe!==r.base_speed||
        p2.baseStoredStats.spe!==r.opponent_speed)
       throw Error("weather selected species, ability or AP reference drift: "+r.id);
     if(r.weather){
       if(!b.field.setWeather(r.weather,p1)||b.field.weather!==r.weather||
          b.field.weatherState.duration!==5)
         throw Error("weather five-turn synthetic precondition drift: "+r.id);
     }else if(b.field.weather)throw Error("unexpected weather in no-weather case");
     if(r.par&&!p1.setStatus("par"))throw Error("synthetic weather paralysis not applied");
     if(r.stage)p1.boosts.spe=r.stage;
     if(r.trickRoom&&!b.field.addPseudoWeather("trickroom",p2))
       throw Error("synthetic weather Trick Room not applied");
     if(p1.status!==(r.par?"par":"")||p1.boosts.spe!==r.stage||
        !!b.field.pseudoWeather.trickroom!==r.trickRoom)
       throw Error("wrong weather preturn status/boost/room");
     const seenSpeed=p1.getStat("spe"),otherSpeed=p2.getStat("spe");
     if(otherSpeed!==r.opponent_speed||
       !r.possible_speeds.includes(seenSpeed)||
       (r.expected_speed!==null&&seenSpeed!==r.expected_speed))
       throw Error("pinned weather effective S outside independent source envelope "+r.id+
         " "+JSON.stringify({seenSpeed,otherSpeed,reference:r}));
     const expected={p1:validateSelectedMove(p1,"move 1","Swords Dance",0,ctx.dex),
       p2:validateSelectedMove(p2,"move 1","Swords Dance",0,ctx.dex)};
     const start=b.log.length;
     b.makeChoices("move 1","move 1");
     const events=b.log.slice(start);
     const caught=paralysisInterruptions(events,
       {g_par:r.par,m_par:false},p1,p2,seed);
     if(caught.length){blocked++;continue;}
     const moved=observeMoves(events,expected,ctx.dex);
     const status=r.expected_first==="UNKNOWN_ROUNDING"?"SOURCE_UNKNOWN_MODIFIER_ORDER":
       moved.first===r.expected_first?"SOURCE_SPEC_MATCH":"SOURCE_SPEC_MISMATCH";
     completed={id:r.id,status,weather:r.weather,ability:r.ability,species:r.species,
       base_speed:r.base_speed,expected_speed:r.expected_speed,
       possible_speeds:r.possible_speeds,pinned_speed:seenSpeed,
       opponent_speed:otherSpeed,expected_first:r.expected_first,
       observed_first:moved.first,observed_sequence:moved.sequence,
       executed_moves:moved.executed_moves,
       synthetic_initial_duration:r.weather?5:null,
       synthetic_after_turn_weather:b.field.weather,
       executed_seed:seed,preempted_paralysis:blocked,
       provenance:"SYNTHETIC_PRETURN_WEATHER_STATUS_BOOST_NOT_DEVICE"};
     break;
   }finally{b.destroy?.();}
 }
 if(!completed)throw Error("no completed moves in fixed weather seed range: "+r.id);
 return completed;
}
function lifecycle(ctx){
 const src=baseFixture();
 const fixture={...src,
   attacker:mon(src.attacker,{moves:["Rain Dance","Swords Dance"],
     evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2}}),
   defender:mon(src.defender,{moves:["Sunny Day","Iron Defense"],
     evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2}})};
 const b=make(ctx,fixture);
 try{
   if(b.p1.active[0].getStat("spe")!==124||
      b.p2.active[0].getStat("spe")!==92)
     throw Error("reviewed weather-overwrite speed precondition drift");
   const selected={
     p1:validateSelectedMove(b.p1.active[0],"move 1","Rain Dance",0,ctx.dex),
     p2:validateSelectedMove(b.p2.active[0],"move 1","Sunny Day",0,ctx.dex)
   };
   const at=b.log.length;
   b.makeChoices("move 1","move 1");
   const log=b.log.slice(at);
   const moves=observeMoves(log,selected,ctx.dex);
   const changes=log.filter(line=>line.startsWith("|-weather|")&&
     !line.includes("|none")&&!line.includes("|[upkeep]"));
   const firstMoveAt=log.indexOf(moves.move_events[0]);
   const firstWeatherAt=log.indexOf(changes[0]);
   const nextMoveAt=log.indexOf(moves.move_events[1]);
   const nextWeatherAt=log.indexOf(changes[1]);
   if(moves.first!=="p1"||
      JSON.stringify(moves.sequence)!==JSON.stringify(["p1","p2"])||
      changes.length!==2||changes[0]!=="|-weather|RainDance"||
      changes[1]!=="|-weather|SunnyDay"||
      !(firstMoveAt>=0&&firstMoveAt<firstWeatherAt&&
        firstWeatherAt<nextMoveAt&&nextMoveAt<nextWeatherAt)||
      b.field.weather!=="sunnyday"||b.field.weatherState.duration!==4)
     throw Error("pinned executed weather move/overwrite order or duration disagrees with source: "+
       JSON.stringify({moves,changes,weather:b.field.weather,duration:b.field.weatherState.duration}));
   const remaining=[b.field.weatherState.duration];
   for(let i=0;i<4;i++){
     if(b.requestState!=="move")throw Error("weather lifecycle ended early");
     b.makeChoices("move 2","move 2");
     remaining.push(b.field.weather?b.field.weatherState.duration:0);
   }
   if(JSON.stringify(remaining)!==JSON.stringify([4,3,2,1,0])||b.field.weather)
     throw Error("pinned 5-turn weather expiry after executed turns drift");
   return {status:"SOURCE_SPEC_MATCH",basis:"TWO_EXECUTED_WEATHER_MOVES_AND_FOUR_FOLLOWUP_TURNS",
     first_weather_event:changes[0],override_weather_event:changes[1],
     executed_move_order:moves.sequence,executed_moves:moves.executed_moves,
     executed_move_events:moves.move_events,
     weather_change_events:changes,
     after_turn_remaining:remaining,extended_item_evaluated:false,
     actual_game_proven:false};
 }finally{b.destroy?.();}
}
function run(){
 checkLedger();
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT needed for pinned isolated weather probe");
 const sha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(sha!==PIN)throw Error("unpinned Showdown weather source "+sha);
 const checked=masterCheck();
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT),allowed=allowedChampionsSets(dex);
 if(dex.currentMod!=="champions")throw Error("unreviewed Showdown format");
 const ctx={Battle,dex,allowed,validator:TeamValidator.get(FORMAT)};
 const results=CASES.map(c=>runCase(ctx,c));
 const counts={SOURCE_SPEC_MATCH:results.filter(r=>r.status==="SOURCE_SPEC_MATCH").length,
   SOURCE_SPEC_MISMATCH:results.filter(r=>r.status==="SOURCE_SPEC_MISMATCH").length,
   SOURCE_UNKNOWN_MODIFIER_ORDER:results.filter(r=>r.status==="SOURCE_UNKNOWN_MODIFIER_ORDER").length};
 return {schema:"phase0-champions-weather-speed-diagnostic-v1",
   pinned_sha:PIN,format:FORMAT,regulation:"M-C",source_evidence:LEDGER.evidence,
   tested_species_count:checked,case_count:results.length,counts,results,
   lifecycle:lifecycle(ctx),weather_duration_rules:LEDGER.weather_duration_rules,
   source_only_not_game:true,game_verified_turns:0,private_golden_read:false,
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",live_use_approved:false,
   unverified:LEDGER.unverified};
}
if(require.main===module){
 try{const r=run();process.stdout.write(JSON.stringify(r)+"\n");
   if(r.counts.SOURCE_SPEC_MISMATCH)process.exitCode=1;
 }catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}
}
module.exports={run,runCase,lifecycle,make,weatherFixture,baseFixture};
