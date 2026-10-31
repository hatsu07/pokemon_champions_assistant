'use strict';
// Offline pinned Showdown-versus-documentation comparison; no live battle use.
// Validate ALL simulator team members before running any calculation.
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {calculate}=require('./wiki_damage_reference.cjs');
const {rows,speciesKeyForRow,types}=require('./audit.cjs');
const PIN='ccfaa20f32af363569cd21a9d501b2bc1ded26b1';
const FORMAT='gen9championsbssregmc';
const ROOT=process.env.SHOWDOWN_ROOT;
const FILLER={species:'Garchomp',level:50,nature:'Serious',ability:'Rough Skin',item:'',
  evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:0},moves:['Earthquake']};
// These bilingual mappings are explicitly scoped to this ONE test fixture.
// Unknown translations must be independently confirmed, never guessed from other games.
const ABILITY_JA=Object.freeze({roughskin:'さめはだ',clearbody:'クリアボディ'});
const STATS=['hp','atk','def','spa','spd','spe'];
const COLUMNS=['H','A','B','C','D','S'];
const MASTER_DIR=path.resolve(process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,'masters'));
function readMaster(name){
  return rows(fs.readFileSync(path.join(MASTER_DIR,name),'utf8'));
}
function allowedChampionsSets(dex){
  const pokemon=readMaster('Pokemon_Champions_MC_usable_pokemon.csv');
  const links=readMaster('Pokemon_Champions_MC_pokemon_learnsets_linked.csv');
  const moveRows=readMaster('Pokemon_Champions_MC_move_master.csv');
  const heldItems=readMaster('Pokemon_Champions_MC_usable_held_items.csv');
  const availableMoves=new Set(moveRows.map(r=>r.MoveKey));
  const availableItems=new Set(heldItems.map(r=>dex.items.get(r['英語名']).id).filter(Boolean));
  const byName=new Map(pokemon.filter(r=>r['区分']!=='メガシンカ'&&r['姿を直接登録']==='○')
    .map(r=>[r['ポケモン名']+'|'+r['フォーム/姿']+'|'+r['区分'],r]));
  const species=new Map();
  const ambiguous=new Set();
  for(const link of links){
    const key=link['ポケモン名']+'|'+link['フォーム/姿']+'|'+link['区分'];
    const row=byName.get(key);
    if(!row)continue;
    const id=speciesKeyForRow(row,link.LearnsetKey);
    const mon=dex.species.get(id);
    if(!mon.exists)continue;
    // Several distinct Champions appearances share one unresolved source key
    // (e.g. Squawkabilly colours). Exclude that key entirely until mapped.
    // Never abort unrelated, unambiguous fixture validation.
    if(ambiguous.has(mon.id))continue;
    if(species.has(mon.id)){
      species.delete(mon.id);
      ambiguous.add(mon.id);
      continue;
    }
    const typeNames=[row['タイプ1'],row['タイプ2']].filter(Boolean).map(t=>types[t]);
    if(typeNames.some(x=>!x)||typeNames.join('/')!==mon.types.join('/'))
      throw Error('Champions type mapping disagrees with pinned Dex: '+mon.id);
    for(let i=0;i<STATS.length;i++)
      if(Number(row[COLUMNS[i]])!==mon.baseStats[STATS[i]])
        throw Error('Champions base stat mapping disagrees with pinned Dex: '+mon.id);
    const learned=new Set((link['習得MoveKey一覧']||'').split(',').map(x=>x.trim()).filter(Boolean));
    species.set(mon.id,{learned,learnsetKey:link.LearnsetKey,abilities:new Set(
      row['特性'].split('/').map(x=>x.trim()).filter(Boolean))});
  }
  return {species,ambiguous,availableMoves,availableItems};
}
function teamsForScenario(s){
  // The exact six sets here are also the sets passed to Battle below.
  const bench=s.bench??[FILLER,FILLER];
  if(!Array.isArray(bench)||bench.length!==2)throw Error('exactly two bench sets are required');
  return [[s.attacker,...bench],[s.defender,...bench]];
}
function validateChampionsScenario(s,dex,allowed){
  const teams=teamsForScenario(s);
  if(!s.move||!dex.moves.get(s.move).exists)throw Error('unknown attack move: '+s.move);
  for(const team of teams)for(const mon of team){
    const record=mon&&dex.species.get(mon.species);
    const entry=record&&allowed.species.get(record.id);
    if(!record?.exists||!entry)
      throw Error('not registered in Champions usable master: '+String(mon?.species));
    // Battle runtime must not silently legalize a missing/inappropriate form.
    if(record.isMega||record.battleOnly)
      throw Error('unsupported battle-only form in starting team: '+mon.species);
    if(!Number.isInteger(mon.level)||mon.level!==50)
      throw Error('simulator-internal level differs from pinned fixture: '+mon.species);
    if(!dex.natures.get(mon.nature).exists)
      throw Error('unrecognized nature: '+mon.species+'/'+mon.nature);
    if(!mon.evs||STATS.some(k=>!Number.isInteger(mon.evs[k])||mon.evs[k]<0||mon.evs[k]>32)||
       STATS.reduce((n,k)=>n+mon.evs[k],0)>66)
      throw Error('invalid Champions AP: '+mon.species);
    const ability=dex.abilities.get(mon.ability);
    const ja=ABILITY_JA[ability.id];
    if(!ability.exists||!ja||!entry.abilities.has(ja)||
       !Object.values(record.abilities).some(id=>dex.abilities.get(id).id===ability.id))
      throw Error('ability absent/unmapped in Champions master and pinned Dex: '+mon.species+'/'+mon.ability);
    if(typeof mon.item!=='string')
      throw Error('unknown item: '+mon.species);
    if(mon.item){
      const item=dex.items.get(mon.item);
      if(!item.exists||!allowed.availableItems.has(item.id))
        throw Error('item absent from Champions held-item master: '+mon.species+'/'+mon.item);
      if(item.megaEvolves&&item.megaEvolves!==record.baseSpecies)
        throw Error('mega stone wrong species: '+mon.species+'/'+mon.item);
    }
    if(!Array.isArray(mon.moves)||mon.moves.length<1||mon.moves.length>4)
      throw Error('invalid move slots: '+mon.species);
    const seen=new Set();
    const showdownLearnset=dex.species.getLearnsetData(entry.learnsetKey)?.learnset||{};
    for(const name of mon.moves){
      const move=dex.moves.get(name);
      if(!move.exists||!allowed.availableMoves.has(move.id)||
         !entry.learned.has(move.id)||!Object.hasOwn(showdownLearnset,move.id))
        throw Error('set move absent from Champions master and pinned learnset: '+mon.species+'/'+name);
      if(seen.has(move.id))throw Error('duplicate move slot: '+mon.species+'/'+name);
      seen.add(move.id);
    }
  }
  const attackMoveId=dex.moves.get(s.move).id;
  if(!teams[0][0].moves.some(m=>dex.moves.get(m).id===attackMoveId))
    throw Error('attack move not equipped on attacker: '+s.attacker.species+'/'+s.move);
  return teams;
}
const SCENARIOS={
  'WIKI-EARTHQUAKE-GARCHOMP-METAGROSS':{
    attacker:{...FILLER,evs:{...FILLER.evs,atk:32}},
    defender:{species:'Metagross',level:50,nature:'Serious',ability:'Clear Body',item:'',
      evs:{...FILLER.evs},moves:['Bullet Punch']},
    move:'Earthquake'
  }
};
function main(){
  if(!ROOT)throw Error('SHOWDOWN_ROOT required; run inside pinned Phase 0 Docker');
  const sha=execFileSync('git',['-C',ROOT,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
  if(sha!==PIN)throw Error('Pinned source mismatch: '+sha);
  const {Battle,Dex}=require(path.join(ROOT,'dist','sim','index.js'));
  const dex=Dex.forFormat(FORMAT);
  if(dex.currentMod!=='champions')throw Error('Champions mod inactive');
  const doc=JSON.parse(fs.readFileSync(path.join(__dirname,'wiki_damage_vectors.json'),'utf8'));
  if(doc.schema!=='champions-wiki-reference-v1')throw Error('unexpected reference schema');
  const allowed=allowedChampionsSets(dex);
  const results=[];
  for(const c of doc.cases){
    const s=SCENARIOS[c.id];
    if(!s)throw Error('No exact scenario mapping for '+c.id);
    const [p1team,p2team]=validateChampionsScenario(s,dex,allowed);
    for(const id of c.source_ids)if(!doc.sources[id]?.url)throw Error('missing source '+id);
    const independent=calculate(c.input);
    if(independent.minimum!==c.expected_min||independent.maximum!==c.expected_max)
      throw Error('published expectation mismatch for '+c.id);
    if(c.comparison_scope!=='PINNED_CHAMPIONS_COMPARABLE')
      throw Error('unreviewed comparison scope for '+c.id);
    const b=new Battle({formatid:FORMAT,seed:[1,2,3,4],strictChoices:true,
      p1:{name:'Source example attacker',team:p1team},
      p2:{name:'Source example defender',team:p2team}});
    if(b.requestState==='teampreview')b.makeChoices('team 123','team 123');
    if(b.format.mod!=='champions')throw Error('runtime mod inactive');
    const a=b.p1.active[0],d=b.p2.active[0];
    if(a.baseStoredStats.atk!==c.input.attack||d.baseStoredStats.def!==c.input.defense)
      throw Error('actual stat precondition mismatch for '+c.id+': '+a.baseStoredStats.atk+'/'+d.baseStoredStats.def);
    const move=dex.moves.get(s.move);
    if(!move.exists||move.category!=='Physical'||move.basePower!==c.input.basePower)
      throw Error('move precondition mismatch for '+c.id);
    const rolls=[];
    for(let pct=85;pct<=100;pct++){
      b.randomizer=base=>b.trunc(b.trunc(base*pct)/100);
      const active=b.dex.getActiveMove(move.id);
      active.willCrit=c.input.critical;
      const damage=b.actions.getDamage(a,d,active,true);
      if(!Number.isInteger(damage))throw Error('noninteger Showdown result for '+c.id);
      rolls.push(damage);
    }
    results.push({id:c.id,evidence:'UNOFFICIAL_WIKI_ARITHMETIC_NOT_GAME_PROOF',
      source_ids:c.source_ids,validated_team_members:6,reference_rolls:independent.rolls,showdown_rolls:rolls,
      status:JSON.stringify(rolls)===JSON.stringify(independent.rolls)?'WIKI_SPEC_MATCH':'WIKI_SPEC_MISMATCH'});
  }
  return {scope:'OFFLINE_DIAGNOSTIC_ONLY',pinned_sha:PIN,format:FORMAT,
    game_observed_level:false,gate_A_calculation:'UNKNOWN',gate_B_reconstruction:'UNSUPPORTED',
    result_count:results.length,comparable_count:results.length,
    mismatch_count:results.filter(x=>x.status==='WIKI_SPEC_MISMATCH').length,results};
}
if(require.main===module){
 try{const out=main();process.stdout.write(JSON.stringify(out,null,2)+'\n');if(out.mismatch_count)process.exitCode=1;}
 catch(e){process.stdout.write(JSON.stringify({status:'NOT_RUN',reason:e.message})+'\n');process.exitCode=2;}
}
module.exports={main,allowedChampionsSets,validateChampionsScenario,SCENARIOS,FILLER};
