"use strict";
// Phase 0 only. Arithmetic and provenance never import or derive expectations from Showdown.
const fs=require("node:fs"),path=require("node:path");
const {rows}=require("./audit.cjs");
const LEDGER=require("./champions_weather_speed_ledger.json");
const SOURCES=Object.freeze([
 ["gamewith_weather","https://gamewith.jp/pokemon-champions/561577","Champions four weather types, five turns, overwrite and weather Speed abilities"],
 ["ability_master","Pokemon_Champions_MC_ability_master.csv","Four Champions ability descriptions explicitly give 2x Speed under matching weather"],
 ["ap_source","https://pkmn-cp-dataportal.com/battle/rule/stat-points/","Neutral AP-based Speed equals species base Speed plus 20 plus AP"],
 ["duration_conflict","https://pkmn-cp-dataportal.com/battle/field/weather-vs-terrain/","Extension item 8-turn claim conflicts with GameWith extension-unavailable entry"]
]);
const SPECIES=Object.freeze([
 ["Qwilfish","ハリーセン","Swift Swim","すいすい","raindance",85],
 ["Excadrill","ドリュウズ","Sand Rush","すなかき","sandstorm",88],
 ["Venusaur","フシギバナ","Chlorophyll","ようりょくそ","sunnyday",80],
 ["Beartic","ツンベアー","Slush Rush","ゆきかき","snowscape",50]
]);
const CASES=Object.freeze([
 ["rain-swift",0,"raindance",false,false,0,false,"p1"],
 ["sun-swift-off",0,"sunnyday",false,false,0,false,"p2"],
 ["no-weather-swift-off",0,"",false,false,0,false,"p2"],
 ["sand-rush",1,"sandstorm",false,false,0,false,"p1"],
 ["rain-rush-off",1,"raindance",false,false,0,false,"p2"],
 ["sun-chlorophyll",2,"sunnyday",false,false,0,false,"p1"],
 ["sand-chlorophyll-off",2,"sandstorm",false,false,0,false,"p2"],
 ["snow-slush",3,"snowscape",false,false,0,false,"p1"],
 ["sun-slush-off",3,"sunnyday",false,false,0,false,"p2"],
 ["snow-slush-par",3,"snowscape",true,false,0,false,"p2"],
 ["sand-rush-trick",1,"sandstorm",false,false,0,true,"p2"],
 ["sand-rush-plusone",1,"sandstorm",false,false,1,false,"p1"],
 ["rain-swift-scarf-unknown",0,"raindance",false,true,0,false,"UNKNOWN_ROUNDING"]
]);
const UNKNOWN=[
 "Actual game weather duration and midturn overwrite",
 "Weather extender items and 8-turn conflict",
 "Modifier order when weather and Choice Scarf produce different rounded Speed",
 "Same-turn weather-setting ability ordering and entry hazards",
 "Ability suppression and simultaneous speed changes",
 "Weather power boosts and weather-related damage rounding"
];
function checkLedger(l=LEDGER){
 if(!l||l.schema!=="phase0-champions-weather-speed-source-v1"||
    l.checked_at!=="2026-11-01"||l.regulation!=="M-C"||
    l.evidence!=="CHAMPIONS_FIXED_MASTER_AND_SECONDARY_SOURCE_NOT_GAME"||
    l.device_turns!==0||l.private_golden_read!==false||l.live_use_approved!==false||
    l.weather_default_turns!==5||
    JSON.stringify(l.source)!==JSON.stringify(SOURCES)||
    JSON.stringify(l.reviewed_species)!==JSON.stringify(SPECIES)||
    JSON.stringify(l.scenarios)!==JSON.stringify(CASES)||
    JSON.stringify(l.unverified)!==JSON.stringify(UNKNOWN)||
    JSON.stringify(l.source_conflicts)!==JSON.stringify([{
      id:"WEATHER_EXTENSION",status:"SOURCE_CONFLICT",
      gamewith_url:SOURCES[0][1],portal_url:SOURCES[3][1],
      impact:"Do not claim extended weather duration is independently confirmed"}]))
   throw Error("weather source evidence contract drift");
 return true;
}
function independentSpeeds({base,weatherBoost,par,scarf,stage}){
 if(!Number.isInteger(base)||base<=0||typeof weatherBoost!=="boolean"||
    typeof par!=="boolean"||typeof scarf!=="boolean"||![0,1].includes(stage))
   throw Error("UNSUPPORTED_WEATHER_SPEED_INPUT");
 const operations=[];
 if(weatherBoost)operations.push([2,1]);
 if(par)operations.push([1,2]);
 if(scarf)operations.push([3,2]);
 if(stage===1)operations.push([3,2]);
 const out=new Set();
 function permute(now,remaining){
   if(!remaining.length){out.add(now);return}
   for(let i=0;i<remaining.length;i++){
     const [a,b]=remaining[i];
     permute(Math.max(1,Math.floor(now*a/b)),remaining.filter((_,j)=>i!==j));
   }
 }
 permute(base,operations);
 const possible=[...out].sort((a,b)=>a-b);
 return {possible,effective:possible.length===1?possible[0]:null,
   status:possible.length===1?"ORDER_INVARIANT":"UNVERIFIED_ROUNDING"};
}
function masterCheck(){
 const root=process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,"masters");
 const mon=rows(fs.readFileSync(path.join(root,"Pokemon_Champions_MC_usable_pokemon.csv"),"utf8"));
 const ability=rows(fs.readFileSync(path.join(root,"Pokemon_Champions_MC_ability_master.csv"),"utf8"));
 for(const [en,ja,abb,aj,weather,base] of SPECIES){
   const candidates=mon.filter(x=>x["ポケモン名"]===ja&&x["区分"]==="通常"&&
      x["フォーム/姿"]==="");
   if(candidates.length!==1||Number(candidates[0].S)!==base||
      !candidates[0]["特性"].split(/\s*\/\s*/).includes(aj))
     throw Error("weather ability species/Speed absent from fixed master: "+en);
   const matches=ability.filter(x=>x["特性名"]===aj);
   const effect=matches.length===1?matches[0]["効果（Champions基準・対戦向け要約）"]:"";
   const label={raindance:"あめ",sandstorm:"すなあらし",sunnyday:"にほんばれ",snowscape:"ゆき"}[weather];
   if(!effect.includes(label)||!effect.includes("素早さが2倍"))
     throw Error("weather boost unsupported in fixed Champions ability master: "+aj);
 }
 return SPECIES.length;
}
function reference(c){
 checkLedger();
 if(!CASES.includes(c))throw Error("weather case not reviewed");
 const [id,index,weather,par,scarf,stage,trickRoom,first]=c;
 const spec=SPECIES[index],base=spec[5]+20+2,other=102+20+2;
 const p1=independentSpeeds({base,weatherBoost:weather===spec[4],par,scarf,stage});
 const p2=other;
 const expected=p1.effective===null?"UNKNOWN_ROUNDING":
   trickRoom?(p1.effective<p2?"p1":"p2"):(p1.effective>p2?"p1":"p2");
 if(expected!==first)throw Error("weather source-order fixture drift: "+id);
 return {id,species:spec[0],ability:spec[2],weather,par,scarf,stage,trickRoom,
   base_speed:base,opponent_speed:other,possible_speeds:p1.possible,
   expected_speed:p1.effective,expected_first:first,
   status:p1.effective===null?"UNKNOWN_ROUNDING":"SOURCE_ARITHMETIC_ONLY"};
}
module.exports={LEDGER,SOURCES,SPECIES,CASES,UNKNOWN,checkLedger,independentSpeeds,masterCheck,reference};
