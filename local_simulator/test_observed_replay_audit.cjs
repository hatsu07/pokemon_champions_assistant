"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {auditSyntheticObservedReplay,SOURCE,sha256}=
  require("./observed_replay_audit.cjs");

function fixture(){
  const battle={kind:"complete-pinned-synthetic",turn:3};
  const packet={
    source:SOURCE,match_id:"synthetic-match",
    decision_window_id:"synthetic-window",state_revision:4,
    battle_sha256:sha256(battle),turn:3,
    active:{p1:1,p2:1},
    members:[
      {side:"p1",slot:1,species_id:"garchomp",hp:100,maxhp:200,
        fainted:false,status:"",boosts:{atk:1}},
      {side:"p2",slot:1,species_id:"dragonite",hp:100,
        fainted:false,status:"par",boosts:{spe:-1}}
    ],
    field:{weather:"",terrain:""}
  };
  const build=()=>({
    format:{mod:"champions"},gameType:"singles",activePerHalf:1,
    requestState:"move",turn:3,field:{weather:"",terrain:""},
    sides:[
      {id:"p1",pokemon:[
        {species:{id:"garchomp"},hp:100,maxhp:200,fainted:false,status:"",
         boosts:{atk:1}},
        {species:{id:"primarina"},hp:80,maxhp:180,fainted:false,status:"",
         boosts:{}},
        {species:{id:"mimikyu"},hp:0,maxhp:100,fainted:true,status:"",
         boosts:{}}],
        active:[]},
      {id:"p2",pokemon:[
        {species:{id:"dragonite"},hp:100,maxhp:100,fainted:false,status:"par",
         boosts:{spe:-1}},
        {species:{id:"gholdengo"},hp:80,maxhp:150,fainted:false,status:"",
         boosts:{}},
        {species:{id:"kingambit"},hp:0,maxhp:100,fainted:true,status:"",
         boosts:{}}],
        active:[]}
    ]
  });
  const Battle={
    fromJSON(_raw){
      const b=build();
      for(const side of b.sides)side.active=[side.pokemon[0]];
      return b;
    }
  };
  return {battle,packet,Battle};
}
function run(packetPatch={},battlePatch={},enginePatch){
  const {battle,packet,Battle}=fixture();
  Object.assign(packet,packetPatch);
  Object.assign(battle,battlePatch);
  if(Object.keys(battlePatch).length)packet.battle_sha256=sha256(battle);
  if(enginePatch){
    const original=Battle.fromJSON;
    Battle.fromJSON=data=>{
      const b=original(data);
      enginePatch(b);
      return b;
    };
  }
  return auditSyntheticObservedReplay(Battle,battle,packet);
}

test("synthetic visible fields are checked, but full state never becomes verified",()=>{
  const result=run();
  assert.equal(result.status,"SYNTHETIC_VISIBLE_FIELDS_MATCH",result.reason);
  assert.ok(result.checked_fields.includes("p1:1.hp"));
  assert.ok(result.checked_fields.includes("p2:1.status"));
  assert.ok(result.checked_fields.includes("p2:1.boosts.spe"));
  assert.equal(result.full_state_reconstructed,false);
  assert.equal(result.exhaustive_observation_verified,false);
  assert.ok(result.unverified.includes("BATTLE_RNG_AND_EVENT_QUEUE_NOT_OBSERVED"));
});
test("missing observations do not silently default to zero or empty status",()=>{
  const {battle,packet,Battle}=fixture();
  delete packet.members[0].boosts;
  delete packet.members[1].status;
  delete packet.members[1].hp;
  const result=auditSyntheticObservedReplay(Battle,battle,packet);
  assert.equal(result.status,"SYNTHETIC_VISIBLE_FIELDS_MATCH");
  assert.equal(result.checked_fields.includes("p2:1.hp"),false);
  assert.equal(result.checked_fields.includes("p2:1.status"),false);
  assert.equal(result.full_state_reconstructed,false);
});
test("mismatch of active slot, numeric HP, status, boosts or field fails closed",()=>{
  const mutations=[
    p=>p.active.p2=2,
    p=>p.members[0].hp=99,
    p=>p.members[0].maxhp=199,
    p=>p.members[0].fainted=true,
    p=>p.members[1].status="",
    p=>p.members[1].boosts.spe=0,
    p=>p.members[0].species_id="dragonite",
    p=>p.field.weather="raindance",
    p=>p.turn=4
  ];
  for(const mutate of mutations){
    const {battle,packet,Battle}=fixture();
    mutate(packet);
    const result=auditSyntheticObservedReplay(Battle,battle,packet);
    assert.equal(result.status,"NO_OBSERVATION_MATCH",result.reason);
    assert.equal(result.full_state_reconstructed,false);
  }
});
test("replay hash and synthetic scope must be explicit and exact",()=>{
  for(const x of [
    {battle_sha256:"0".repeat(64)},
    {source:"REAL_GAME_CAPTURE"},
    {source:"OCR_CONFIRMED"},
    {match_id:""},
    {decision_window_id:""},
    {state_revision:-1},
  ]){
    const result=run(x);
    assert.equal(result.status,"NO_OBSERVATION_MATCH");
  }
  assert.equal(run({}, {turn:99}).status,"NO_OBSERVATION_MATCH");
});
test("malformed data, unknown fields, duplication and ambiguous HP are rejected",()=>{
  const edits=[
    p=>p.members[0].hp="100/200",
    p=>p.members[0].hp=NaN,
    p=>p.members[0].hp=true,
    p=>p.members[0].hp=201,
    p=>p.members[1].slot=4,
    p=>p.members.push({...p.members[0]}),
    p=>p.members[0].item="hidden",
    p=>p.members[0].boosts.acc=1,
    p=>p.members[0].boosts.atk=7,
    p=>p.members[0].boosts={},
    p=>p.members=[],
    p=>p.active={p1:1},
    p=>p.field={weather:null},
    p=>p.field={unknown:"sunny"},
    p=>p.unreviewed_extra="truth",
  ];
  for(const mutate of edits){
    const {battle,packet,Battle}=fixture();
    mutate(packet);
    const result=auditSyntheticObservedReplay(Battle,battle,packet);
    assert.equal(result.status,"NO_OBSERVATION_MATCH");
  }
});
test("unsupported simulator and malformed roster cannot be used as evidence",()=>{
  const {battle,packet,Battle}=fixture();
  assert.equal(auditSyntheticObservedReplay(null,battle,packet).status,
               "NO_OBSERVATION_MATCH");
  for(const change of [
    b=>b.format.mod="gen9",
    b=>b.gameType="doubles",
    b=>b.sides[1].pokemon.pop(),
    b=>b.sides[0].active=[b.sides[0].pokemon[1]],
  ]){
    const result=run({}, {}, change);
    assert.equal(result.status,"NO_OBSERVATION_MATCH");
  }
});
