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

// Pin the full set of *individual* synthetic checks. Detect early-return case loss,
// duplicate IDs and any unexpected transition to MISMATCH or INCONCLUSIVE.
const EVENT_BASELINE=require('./champions_event_baseline.json');
function assertEventBaseline(report){
  assert.equal(EVENT_BASELINE.schema,'champions-event-probe-baseline-v1');
  assert.equal(report.pinned_sha,EVENT_BASELINE.pinned_sha);
  const actual=report.results.map(({id,rule_id,status,observation_basis})=>
    ({id,rule_id,status,observation_basis}));
  assert.equal(new Set(actual.map(x=>x.id)).size,actual.length,'duplicate event ID');
  assert.deepEqual(actual,EVENT_BASELINE.cases,'required event cases or outcomes changed');
  const statusCount=(status)=>actual.filter(x=>x.status===status).length;
  assert.deepEqual(report.counts,{
    WIKI_SPEC_MATCH:statusCount('WIKI_SPEC_MATCH'),
    WIKI_SPEC_MISMATCH:statusCount('WIKI_SPEC_MISMATCH'),
    INCONCLUSIVE:statusCount('INCONCLUSIVE')
  },'reported status counts differ from case results');
  const kinds=['EXECUTED_TURN','REQUEST_AFTER_TURN','SOURCE_ONLY'];
  assert.deepEqual(report.observation_basis_counts,Object.fromEntries(kinds.map(kind=>
    [kind,actual.filter(x=>x.observation_basis===kind).length])));
  assert.ok(actual.some(x=>x.observation_basis==='SOURCE_ONLY'&&x.status==='INCONCLUSIVE'));
}
test('all 18 pinned events are present with precisely the reviewed statuses and evidence basis',()=>{
  assertEventBaseline(main());
});
test('baseline regression fails closed for missing, duplicated, mismatched and newly inconclusive cases',()=>{
  const original=main();
  const variants=[
    report=>{report.results.splice(report.results.findIndex(x=>x.id==='herb-first-drop'),1);},
    report=>{report.results.push({...report.results[0]});},
    report=>{report.results.find(x=>x.id==='scarf-switch-unlock').status='WIKI_SPEC_MISMATCH';},
    report=>{report.results.find(x=>x.id==='herb-second-drop').status='INCONCLUSIVE';},
    report=>{report.results.find(x=>x.id==='scarf-other-move').observation_basis='SOURCE_ONLY';},
    report=>{report.results[0].id='new-unreviewed-case';}
  ];
  for(const mutate of variants){
    const broken=structuredClone(original);
    mutate(broken);
    assert.throws(()=>assertEventBaseline(broken),/event|required|duplicate|counts/i);
  }
});
