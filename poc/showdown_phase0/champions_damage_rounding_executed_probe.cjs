"use strict";
// Isolated Phase 0 synthetic execution of 16 reviewed random rolls. Never device parity.
const path=require("node:path"),{execFileSync}=require("node:child_process");
const {SCENARIOS,validateChampionsScenario,allowedChampionsSets}=require("./wiki_damage_compare.cjs");
const {validateSelectedMove,observeMoves,PIN,FORMAT}=require("./champions_turn_order_probe.cjs");
const {LEDGER:PR160,WITNESS_ID}=require("./champions_damage_modifier_probe.cjs");
const {LEDGER,checkLedger,secondaryComparison}=require("./champions_damage_rounding_secondary_reference.cjs");
function reviewedFixture(){
 const source=SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
 if(source?.attacker?.species!=="Garchomp"||
   source?.defender?.species!=="Metagross"||
   source.p2bench?.[2]?.species!=="Blastoise")
   throw Error("PR160 exact registered Blastoise fixture drift");
 const bench=[...source.p2bench];bench[2]=source.defender;
 return {...source,defender:source.p2bench[2],p2bench:bench};
}
function executePct(ctx,row){
 const {Battle,dex,validator,allowed}=ctx;
 const fixture=reviewedFixture(),teams=validateChampionsScenario(fixture,dex,allowed);
 for(const team of teams){
   const bad=validator.validateTeam(structuredClone(team));
   if(bad?.length)throw Error("M-C registered-six damage-turn fixture illegal: "+bad.join(" | "));
 }
 for(let seed=1;seed<=48;seed++){
   const b=new Battle({formatid:FORMAT,seed:[3,4,5,seed],strictChoices:true,
      p1:{name:"Synthetic damage attacker",team:teams[0]},
      p2:{name:"Synthetic damage defender",team:teams[1]}});
   try{
     if(b.requestState==="teampreview")b.makeChoices("team 123","team 123");
     const a=b.p1.active[0],d=b.p2.active[0];
     if(b.requestState!=="move"||b.p1.pokemon.length!==3||
       b.p2.pokemon.length!==3||a.species.id!=="garchomp"||
       d.species.id!=="blastoise"||
       a.baseStoredStats.atk!==PR160.fixture.atk||
       d.baseStoredStats.def!==120||a.ability!=="roughskin"||
       d.ability!=="torrent"||d.types.join("/")!=="Water")
       throw Error("pinned selected exact stats/abilities/types drift");
     if(!a.setStatus("brn",d)||a.status!=="brn"||
       !b.p2.addSideCondition("reflect",d)||!b.p2.getSideCondition("reflect"))
       throw Error("synthetic burn/Reflect preturn setup failed");
     if(a.item||d.item||b.field.weather||a.boosts.atk||d.boosts.def)
       throw Error("unreviewed item/weather/stage in executed damage fixture");
     const selected={
       p1:validateSelectedMove(a,"move 1","Earthquake",0,dex),
       p2:validateSelectedMove(d,"move 1","Surf",0,dex)
     };
     // The override enforces a desired getDamage damage roll in PINNED SHOWDOWN.
     // It is NOT evidence that the real game samples these rolls in this way.
     b.randomizer=base=>b.trunc(b.trunc(base*row.roll_pct)/100);
     const preview=b.dex.getActiveMove("earthquake");preview.willCrit=false;
     const pinnedDirect=b.actions.getDamage(a,d,preview,true);
     if(pinnedDirect!==row.sequential)
       throw Error("pinned getDamage no longer matches the audited sequential boundary "+row.roll_pct);
     const initial=d.hp,at=b.log.length;
     b.makeChoices("move 1","move 1");
     const log=b.log.slice(at);
     if(log.some(line=>line.startsWith("|-crit|")||line.startsWith("|-miss|"))){
       // A natural critical is an unreviewed branch. Retry a fixed, different seed.
       continue;
     }
     const observed=observeMoves(log,selected,dex);
     if(observed.first!=="p1")
       throw Error("source fixture p1 must attack before p2 for direct HP attribution");
     const dmgEvents=log.filter(line=>line.startsWith("|-damage|p2a: Blastoise|"));
     const earthquakeAt=log.indexOf(observed.move_events[0]);
     const splitAt=log.indexOf("|split|p2",earthquakeAt+1);
     const damageAt=log.indexOf(dmgEvents[0],splitAt+1);
     const percentageAt=log.indexOf(dmgEvents[1],damageAt+1);
     const surfAt=log.indexOf(observed.move_events[1]);
     // Showdown's |split|p2 publishes one exact HP and one percent view.
     // These two lines are ONE damage event, not two executed hits.
     const exact=dmgEvents[0]?.match(/^\|-damage\|p2a: Blastoise\|(\d+)\/(\d+)$/);
     const percent=dmgEvents[1]?.match(/^\|-damage\|p2a: Blastoise\|(\d+)\/100$/);
     if(dmgEvents.length!==2||!exact||!percent||
        Number(exact[2])!==d.maxhp||Number(exact[1])!==d.hp||
        Number(percent[1])!==Math.round(100*d.hp/d.maxhp)||
        !(earthquakeAt>=0&&earthquakeAt<splitAt&&splitAt<damageAt&&
          damageAt<percentageAt&&percentageAt<surfAt))
       throw Error("executed split HP event not attributable to Earthquake: "+
         JSON.stringify({pct:row.roll_pct,dmgEvents,earthquakeAt,splitAt,damageAt,percentageAt,surfAt,log}));
     const actual=initial-d.hp;
     if(actual!==pinnedDirect||
       actual!==row.sequential||actual<1||
       !b.p2.getSideCondition("reflect"))
       throw Error("executed HP delta vs pinned getDamage and final-step rounding disagrees "+
         JSON.stringify({roll_pct:row.roll_pct,initial,final:d.hp,pinnedDirect,actual,log}));
     return {roll_pct:row.roll_pct,seed,actual_hp_delta:actual,
       pinned_direct_damage:pinnedDirect,secondary_combined:row.combined,
       secondary_sequential:row.sequential,
       observed_moves:observed.executed_moves,
       observed_move_order:observed.sequence,
       event_trace:[observed.move_events[0],"|split|p2",...dmgEvents,observed.move_events[1]],
       status:row.discriminating?"EXECUTED_MATCHES_SEQUENTIAL_DIFFERS_COMBINED":
        "EXECUTED_MATCHES_BOTH_SECONDARY_FINAL_STEPS",
       provenance:"PINNED_SYNTHETIC_EXECUTED_TURN_BURN_REFLECT_NOT_DEVICE"};
   }finally{b.destroy?.();}
 }
 throw Error("no noncritical fully executed seeded roll for "+row.roll_pct);
}
function run(){
 checkLedger();
 const root=process.env.SHOWDOWN_ROOT;
 if(!root)throw Error("SHOWDOWN_ROOT required");
 const sha=execFileSync("git",["-C",root,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
 if(sha!==PIN)throw Error("unpinned Showdown for damage executed turn");
 const {Battle,Dex,TeamValidator}=require(path.join(root,"dist","sim","index.js"));
 const dex=Dex.forFormat(FORMAT);
 if(dex.currentMod!=="champions")throw Error("pinned Champions M-C mod absent");
 const ctx={Battle,dex,validator:TeamValidator.get(FORMAT),
    allowed:allowedChampionsSets(dex)};
 const secondary=secondaryComparison();
 const results=secondary.rows.map(row=>executePct(ctx,row));
 const exceptional=results.filter(x=>x.status==="EXECUTED_MATCHES_SEQUENTIAL_DIFFERS_COMBINED");
 if(JSON.stringify(exceptional.map(x=>x.roll_pct))!==
    JSON.stringify(LEDGER.specific_scope.witness_rolls))
   throw Error("executed-turn damage divergence changed");
 return {schema:"phase0-champions-damage-rounding-executed-v1",pinned_sha:PIN,
   format:FORMAT,regulation:"M-C",source_evidence:LEDGER.evidence,
   scope:"16_FIXED_RNG_SYNTHETIC_EXECUTED_TURNS_ONE_LEGAL_BLASTOISE_FIXTURE",
   source_ids:secondary.source_ids,roll_count:16,executed_turn_count:16,
   discriminating_witness_count:4,source_conflict_at_final_step:true,
   results,game_observed_turns:0,private_golden_read:false,
   live_use_approved:false,gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",
   adoption:"NO_GO",unverified:LEDGER.unverified};
}
if(require.main===module){
 try{process.stdout.write(JSON.stringify(run())+"\n");}
 catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}
}
module.exports={run,executePct,reviewedFixture};
