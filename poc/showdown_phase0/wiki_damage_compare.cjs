'use strict';
// Offline pinned Showdown-versus-documentation comparison, never a live engine.
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {calculate}=require('./wiki_damage_reference.cjs');
const PIN='ccfaa20f32af363569cd21a9d501b2bc1ded26b1';
const FORMAT='gen9championsbssregmc';
const ROOT=process.env.SHOWDOWN_ROOT;
const FILLER={species:'Garchomp',level:50,nature:'Serious',ability:'Rough Skin',item:'',
  evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:0},moves:['Earthquake']};
// Fixed synthetic sets: only named published examples, not the user's private battle data.
const SCENARIOS={
  'WIKI-EARTHQUAKE-GARCHOMP-METAGROSS':{
    attacker:{...FILLER,evs:{...FILLER.evs,atk:32}},
    defender:{species:'Metagross',level:50,nature:'Serious',ability:'Clear Body',item:'',
      evs:{...FILLER.evs},moves:['Tackle']},move:'Earthquake'},
  'WIKI-STONEEDGE-GARCHOMP-CRESSELIA':{
    attacker:{...FILLER,evs:{...FILLER.evs,atk:32},moves:['Stone Edge']},
    defender:{species:'Cresselia',level:50,nature:'Impish',ability:'Levitate',item:'',
      evs:{...FILLER.evs,def:32},moves:['Tackle']},move:'Stone Edge'},
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
  const results=[];
  for(const c of doc.cases){
    const s=SCENARIOS[c.id];
    if(!s)throw Error('No exact scenario mapping for '+c.id);
    for(const id of c.source_ids)if(!doc.sources[id]?.url)throw Error('missing source '+id);
    const independent=calculate(c.input);
    if(independent.minimum!==c.expected_min||independent.maximum!==c.expected_max)
      throw Error('published expectation mismatch for '+c.id);
    const b=new Battle({formatid:FORMAT,seed:[1,2,3,4],strictChoices:true,
      p1:{name:'Source example attacker',team:[s.attacker,FILLER,FILLER]},
      p2:{name:'Source example defender',team:[s.defender,FILLER,FILLER]}});
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
      source_ids:c.source_ids,reference_rolls:independent.rolls,showdown_rolls:rolls,
      status:JSON.stringify(rolls)===JSON.stringify(independent.rolls)?'WIKI_SPEC_MATCH':'WIKI_SPEC_MISMATCH'});
  }
  return {scope:'OFFLINE_DIAGNOSTIC_ONLY',pinned_sha:PIN,format:FORMAT,
    game_observed_level:false,gate_A_calculation:'UNKNOWN',gate_B_reconstruction:'UNSUPPORTED',
    result_count:results.length,mismatch_count:results.filter(x=>x.status==='WIKI_SPEC_MISMATCH').length,
    results};
}
if(require.main===module){
 try{const out=main();process.stdout.write(JSON.stringify(out,null,2)+'\n');if(out.mismatch_count)process.exitCode=1;}
 catch(e){process.stdout.write(JSON.stringify({status:'NOT_RUN',reason:e.message})+'\n');process.exitCode=2;}
}
module.exports={main};
