'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {main,compare,validate}=require('./champions_event_probe.cjs');
const {allowedChampionsSets}=require('./wiki_damage_compare.cjs');
const {Dex,TeamValidator}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
const FORMAT='gen9championsbssregmc';
test('five source contracts observe real pinned turns; unsupported conditions are inconclusive',()=>{
  const r=main();
  assert.equal(r.schema,'champions-showdown-event-diagnostics-v1');
  assert.equal(r.pinned_sha,'ccfaa20f32af363569cd21a9d501b2bc1ded26b1');
  assert.deepEqual([r.gate_A,r.gate_B,r.adoption],['UNKNOWN','UNSUPPORTED','NO_GO']);
  assert.equal(new Set(r.results.map(x=>x.rule_id)).size,5);
  for(const rule of new Set(r.results.map(x=>x.rule_id))){
    assert.ok(r.results.some(x=>x.rule_id===rule&&x.events.some(e=>e.startsWith('|move|p1a:'))),rule);
  }
  assert.ok(r.results.some(x=>x.id==='knock-contact-faint'&&x.status==='WIKI_SPEC_MATCH'));
  assert.ok(r.results.some(x=>x.id==='mega-reset'&&x.actual_state.mega_types.join('/')==='Water/Dark'));
  for(const x of r.results){
    assert.ok(x.source.url.includes('bulbapedia.bulbagarden.net'));
    if(x.status==='WIKI_SPEC_MATCH')assert.equal(x.mismatched_fields.length,0);
    if(x.status==='INCONCLUSIVE')assert.ok(x.reason&&x.unverified_conditions.length);
  }
  assert.ok(r.counts.INCONCLUSIVE>=3);
});
test('same fixed seed reproduces all event projections and source comparisons',()=>{
  const a=main([7,11,13,17]),b=main([7,11,13,17]);
  assert.deepEqual(a,b);
});
test('classification does not turn missing evidence into match',()=>{
  const source={status:'SOURCE_RULE_SIMULATION_ONLY',state:{item:null}};
  const args={before:{item:'whiteherb'},action:{kind:'use-move'},reference:source,
    expected:{item:null},actual:{item:'whiteherb'},events:['|move|p1a: Lucario|Close Combat|p2a: Primarina']};
  const mismatch=compare('test','WHITE_HERB_ON_LOWERED_STATS',args);
  assert.equal(mismatch.status,'WIKI_SPEC_MISMATCH');
  assert.deepEqual(mismatch.mismatched_fields,[{field:'item',expected:null,actual:'whiteherb'}]);
  const incomplete=compare('test','WHITE_HERB_ON_LOWERED_STATS',{...args,reason:'Move not executed'});
  assert.equal(incomplete.status,'INCONCLUSIVE');
  assert.equal(incomplete.mismatched_fields.length,0);
});
test('unlearnable or unknown move cannot enter the pinned fixture',()=>{
  const dex=Dex.forFormat(FORMAT),allowed=allowedChampionsSets(dex);
  const validator=TeamValidator.get(FORMAT);
  const base={level:50,nature:'Serious',item:'',evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2}};
  const team=[
    {...base,species:'Meowscarada',ability:'Protean',moves:['Ice Beam']},
    {...base,species:'Primarina',ability:'Torrent',moves:['Surf']},
    {...base,species:'Lucario',ability:'Inner Focus',moves:['Close Combat']},
    {...base,species:'Venusaur',ability:'Overgrow',moves:['Giga Drain']},
    {...base,species:'Blastoise',ability:'Torrent',moves:['Surf']},
    {...base,species:'Charizard',ability:'Blaze',moves:['Flamethrower']},
  ];
  assert.throws(()=>validate(team,dex,allowed,validator),/unlearnable\/unknown Champions move/);
  team[0].moves=['Uninvented Attack'];
  assert.throws(()=>validate(team,dex,allowed,validator),/unlearnable\/unknown Champions move/);
});
