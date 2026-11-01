"use strict";
// Phase 0 pinned-Showdown comparison against an independent Champions AP formula.
// Only documented ordinary registered bodies; no game-observed provenance.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {calculate,validatePublished,STATS,STATUS,LEDGER}=require("./champions_ap_stat_reference.cjs");
const {rows}=require("./audit.cjs");
const {allowedChampionsSets,teamsForScenario,validateChampionsRegisteredTeam,
  SCENARIOS}=require("./wiki_damage_compare.cjs");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT="gen9championsbssregmc";
const AP_POINTS=[0,1,7,8,18,28,31,32];
const AP_TARGETS=["hp","atk","spe"];
const NATURES=["Serious","Jolly","Adamant","Brave"];
const SPECIES=[
 {name:"Garchomp",japanese:"ガブリアス",side:"p1"},
 {name:"Metagross",japanese:"メタグロス",side:"p2"}
];
function masterStats(ja){
 const dir=process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,"masters");
 const list=rows(fs.readFileSync(path.join(dir,"Pokemon_Champions_MC_usable_pokemon.csv"),"utf8"));
 const selected=list.filter(r=>r["ポケモン名"]===ja&&r["区分"]==="通常"&&r["フォーム/姿"]==="");
 if(selected.length!==1)throw Error("Champions master species not unique: "+ja);
 const keys={hp:"H",atk:"A",def:"B",spa:"C",spd:"D",spe:"S"};
 const stats=Object.fromEntries(STATS.map(k=>[k,Number(selected[0][keys[k]])]));
 if(Object.values(stats).some(n=>!Number.isSafeInteger(n)||n<1||n>255))
   throw Error("invalid fixed Champions master stats: "+ja);
 return stats;
}
function assess(expected,actual){
 if(!expected||!actual)throw Error("stat comparison lacks expected or actual state");
 const missing=STATS.filter(k=>!Number.isSafeInteger(actual[k])||actual[k]<1);
 if(missing.length)throw Error("unreadable pinned battle stat: "+missing.join(","));
 const differences=STATS.filter(k=>expected[k]!==actual[k])
   .map(k=>({stat:k,reference:expected[k],pinned_showdown:actual[k]}));
 return {status:differences.length?"SOURCE_SPEC_MISMATCH":"SOURCE_SPEC_MATCH",differences};
}
function run(){
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT required, no implicit external engine");
 const sha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(sha!==PIN)throw Error("unpinned Showdown revision: "+sha);
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT);
 if(dex.currentMod!=="champions")throw Error("wrong Showdown mod");
 const validator=TeamValidator.get(FORMAT);
 const allowed=allowedChampionsSets(dex);
 const publishedChecks=validatePublished();
 const scenario=SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
 if(!scenario)throw Error("six-member scenario not found");
 const cases=[];
 for(const selected of SPECIES){
   const baseStats=masterStats(selected.japanese);
   for(const nature of NATURES)for(const stat of AP_TARGETS)for(const points of AP_POINTS){
     const registered=teamsForScenario(scenario);
     const side=selected.side==="p1"?0:1;
     const set=registered[side][0];
     if(set.species!==selected.name)throw Error("scenario active species drift");
     set.nature=nature;
     // Pinned M-C TeamValidator rejects a registered Serious Pokemon with
     // exactly zero AP. Keep selected AP target at zero but assign 2 AP to
     // independent SpD for that single edge case; both methods see same AP.
     set.evs=Object.fromEntries(STATS.map(k=>[
       k,k===stat?points:(points===0&&k==="spd"?2:0)
     ]));
     const ref=calculate({baseStats,ap:set.evs,nature});
     if(ref.status!==STATUS)throw Error("independent ref status changed");
     for(const team of registered){
       validateChampionsRegisteredTeam(team,dex,allowed);
       const problems=validator.validateTeam(structuredClone(team));
       if(problems?.length)throw Error("pinned format rejected legal synthetic registered team: "+problems.join(" | "));
     }
     const b=new Battle({formatid:FORMAT,seed:[1,2,3,4],strictChoices:true,
       p1:{name:"Source stat reference A",team:registered[0]},
       p2:{name:"Source stat reference B",team:registered[1]}});
     if(b.requestState==="teampreview")b.makeChoices("team 123","team 123");
     if(b.format.mod!=="champions")throw Error("battle did not use Champions mod");
     const mon=selected.side==="p1"?b.p1.active[0]:b.p2.active[0];
     if(!mon||mon.species.id!==dex.species.get(selected.name).id)
       throw Error("selected synthetic active changed");
     const result=assess(ref.stats,mon.baseStoredStats);
     cases.push({id:[selected.name,nature,stat,points].join("/"),
       species:selected.name,nature,ap_target:stat,ap_points:points,
       status:result.status,differences:result.differences});
     b.destroy?.();
   }
 }
 const counts={SOURCE_SPEC_MATCH:cases.filter(c=>c.status==="SOURCE_SPEC_MATCH").length,
   SOURCE_SPEC_MISMATCH:cases.filter(c=>c.status==="SOURCE_SPEC_MISMATCH").length};
 return {schema:"phase0-champions-ap-stat-source-comparison-v1",
   pinned_sha:PIN,format:FORMAT,regulation:"M-C",
   evidence:"SECONDARY_CHAMPIONS_SOURCE_NOT_DEVICE_GOLDEN",
   source_ids:LEDGER.sources.map(x=>x.id),source_checked_at:LEDGER.checked_at,
   official_or_game_confirmed:false,private_golden_read:false,game_case_count:0,
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
   registered_six_member_teams:true,selected_three_member_teams:true,
   species_count:SPECIES.length,nature_count:NATURES.length,ap_values:AP_POINTS,
   ap_targets:AP_TARGETS,published_speed_reference_checks:publishedChecks,
   case_count:cases.length,individual_stat_comparisons:cases.length*STATS.length,
   counts,first_mismatches:cases.filter(c=>c.status==="SOURCE_SPEC_MISMATCH").slice(0,20),
   sample_case_ids:cases.filter((_,i)=>i%24===0).map(c=>c.id),
   unverified:LEDGER.not_verified,live_use_approved:false};
}
if(require.main===module){
 try{
   const r=run();
   process.stdout.write(JSON.stringify(r)+"\n");
   if(r.counts.SOURCE_SPEC_MISMATCH)process.exitCode=1;
 }catch(e){
   process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");
   process.exitCode=2;
 }
}
module.exports={run,assess,masterStats,AP_POINTS,AP_TARGETS,NATURES,SPECIES,PIN};
