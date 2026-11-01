"use strict";
// Independent Champions-source arithmetic; DO NOT IMPORT SHOWDOWN HERE.
const LEDGER=require("./champions_ap_stat_source_ledger.json");
const STATS=["hp","atk","def","spa","spd","spe"];
const NATURES=Object.freeze(LEDGER.supported_natures);
const STATUS="CHAMPIONS_COMMUNITY_SOURCE_ARITHMETIC_ONLY";
function exactly(obj,keys,label){
 if(!obj||typeof obj!=="object"||Array.isArray(obj)||
    Object.keys(obj).length!==keys.length||keys.some(k=>!Object.hasOwn(obj,k)))
    throw Error("unknown or incomplete "+label);
}
function calculate({baseStats,ap,nature}){
 exactly(baseStats,STATS,"Champions base stats");
 exactly(ap,STATS,"Champions AP points");
 if(!Object.hasOwn(NATURES,nature))throw Error("unreviewed Champions nature");
 const mods=NATURES[nature];
 if(mods.plus!==null&&(!STATS.includes(mods.plus)||mods.plus==="hp"||
    mods.plus===mods.minus))throw Error("bad reference nature plus");
 if(mods.minus!==null&&(!STATS.includes(mods.minus)||mods.minus==="hp"))
    throw Error("bad reference nature minus");
 for(const k of STATS){
   if(!Number.isSafeInteger(baseStats[k])||baseStats[k]<1||baseStats[k]>255)
      throw Error("invalid Champions species base stat: "+k);
   if(!Number.isInteger(ap[k])||ap[k]<0||ap[k]>32)
      throw Error("invalid Champions AP: "+k);
 }
 if(STATS.reduce((sum,k)=>sum+ap[k],0)>66)throw Error("Champions AP total over 66");
 const stats=Object.fromEntries(STATS.map(k=>{
   const unmodified=baseStats[k]+(k==="hp"?75:20)+ap[k];
   // Floor at exactly the documented nature multiplier step.
   const score=mods.plus===k?Math.floor(unmodified*11/10):
     mods.minus===k?Math.floor(unmodified*9/10):unmodified;
   return [k,score];
 }));
 return {status:STATUS,regulation:"M-C",game_observed_level:false,
   internal_stat_level:50,source_ids:LEDGER.sources.map(s=>s.id),stats};
}
function validatePublished(){
 if(LEDGER.schema!=="phase0-champions-ap-stat-source-ledger-v1"||
    LEDGER.regulation!=="M-C"||LEDGER.game_observed_match_count!==0||
    LEDGER.live_use_approved!==false||
    LEDGER.sources.length!==2||
    LEDGER.sources.some(x=>!x.url.startsWith("https://"))||
    LEDGER.independent_published_garchomp_speed.length!==7)
    throw Error("independent Champions AP source ledger drift");
 const base={hp:108,atk:130,def:95,spa:80,spd:85,spe:102};
 for(const v of LEDGER.independent_published_garchomp_speed){
   if(!Number.isInteger(v.ap)||v.ap<0||v.ap>32)throw Error("bad published AP");
   const ap={hp:0,atk:0,def:0,spa:0,spd:0,spe:v.ap};
   const neutral=calculate({baseStats:base,ap,nature:"Serious"}).stats.spe;
   const jolly=calculate({baseStats:base,ap,nature:"Jolly"}).stats.spe;
   if(neutral!==v.neutral||jolly!==v.jolly)
     throw Error("published Garchomp speed example changed at AP "+v.ap);
 }
 return LEDGER.independent_published_garchomp_speed.length*2;
}
module.exports={calculate,validatePublished,STATS,NATURES,STATUS,LEDGER};
