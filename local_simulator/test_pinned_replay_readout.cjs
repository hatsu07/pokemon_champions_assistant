"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {extractPinnedReplayReadout,SOURCE,sha}=
  require("./pinned_replay_readout.cjs");

function fixture(){
  const raw={turn:3,seed:[1,2,3,4],type:"complete-synthetic"};
  const mk=(id,hp=80,boosts={})=>({
    species:{id},moveSlots:[{id:"protect"},{id:"earthquake"}],
    hp,maxhp:100,fainted:hp===0,
    boosts:Object.fromEntries(["atk","def","spa","spd","spe",
                                "accuracy","evasion"].map(k=>[k,boosts[k]||0])),
  });
  const build=(serialized)=>{
    const a={id:"p1",pokemon:[mk("garchomp",88,{atk:2}),
      mk("primarina"),mk("mimikyu")]};
    const b={id:"p2",pokemon:[mk("dragonite",75),
      mk("gholdengo",0),mk("kingambit")]};
    a.active=[a.pokemon[0]];
    b.active=[b.pokemon[2]];
    return {format:{mod:"champions"},gameType:"singles",
      activePerHalf:1,requestState:"move",sides:[a,b],
      toJSON:()=>JSON.parse(JSON.stringify(serialized))};
  };
  return {raw, Battle:{fromJSON:serialized=>build(serialized)}};
}
test("read-only synthetic projection has six members and no certification",()=>{
  const {raw,Battle}=fixture(),before=JSON.stringify(raw);
  const out=extractPinnedReplayReadout(Battle,raw);
  assert.equal(out.status,"SYNTHETIC_ENGINE_REPLAY_READOUT",out.reason);
  assert.equal(out.source,SOURCE);
  assert.equal(out.battle_sha256,sha(raw));
  assert.deepEqual(out.active,{p1:1,p2:3});
  assert.equal(out.members.length,6);
  assert.deepEqual(out.members[0].move_ids,["protect","earthquake"]);
  assert.equal(out.members[0].boosts[0][1],2);
  assert.equal(out.members[4].fainted,true);
  assert.equal(out.full_state_reconstructed,false);
  assert.equal(out.ready_for_engine,false);
  assert.equal(out.opponent_slot_mapping_authenticated,false);
  assert.equal(JSON.stringify(raw),before);
});
test("malformed engine, unsupported scope and changed roundtrip refuse",()=>{
  const {raw,Battle}=fixture();
  const cases=[
    [null,raw,"COMPLETE_SYNTHETIC_BATTLE_REQUIRED"],
    [Battle,[], "COMPLETE_SYNTHETIC_BATTLE_REQUIRED"],
    [{fromJSON:()=>({...Battle.fromJSON(raw),gameType:"doubles"})},
      raw,"UNSUPPORTED_ENGINE_BATTLE_SCOPE"],
    [{fromJSON:()=>({...Battle.fromJSON(raw),toJSON:()=>({turn:999})})},
      raw,"ENGINE_REPLAY_ROUNDTRIP_MISMATCH"],
    [{fromJSON:()=>{throw Error("broken")}},raw,
      "ENGINE_REPLAY_READOUT_UNAVAILABLE"],
  ];
  for(const [engine,state,reason] of cases){
    assert.equal(extractPinnedReplayReadout(engine,state).reason,reason);
  }
});
test("no accidental guessing of missing boost/move/active roster details",()=>{
  const {raw,Battle}=fixture();
  for(const edit of [
    b=>delete b.sides[0].pokemon[0].boosts.spe,
    b=>b.sides[0].pokemon[0].moveSlots=[],
    b=>b.sides[0].pokemon[0].species.id="",
    b=>b.sides[0].pokemon[0].hp=-1,
    b=>b.sides[0].active=[{species:{id:"garchomp"}}],
    b=>b.sides[1].pokemon.pop(),
  ]){
    const broken={fromJSON:()=>{const b=Battle.fromJSON(raw);edit(b);return b;}};
    const x=extractPinnedReplayReadout(broken,raw);
    assert.equal(x.status,"NO_ENGINE_REPLAY_READOUT",x.reason);
    assert.equal(x.ready_for_engine,false);
  }
});
test("exact JSON text SHA is invariant across Python 3.0 and JS numeric-key normalization",()=>{
  const {Battle}=fixture();
  const raw='{"condition":{"10":3,"2":5,"a":1},"seed":[1,2,3,4],"turn":3.0}';
  const data=JSON.parse(raw);
  const out=extractPinnedReplayReadout(Battle,raw);
  const expected=require("node:crypto").createHash("sha256")
    .update(raw,"utf8").digest("hex");
  assert.equal(out.status,"SYNTHETIC_ENGINE_REPLAY_READOUT",out.reason);
  assert.equal(out.battle_sha256,expected);
  assert.notEqual(out.battle_sha256,sha(data));
  assert.equal(out.members.length,6);
  assert.equal(out.ready_for_engine,false);
});
test("different hidden RNG with unchanged visible values hashes differently",()=>{
  const {raw,Battle}=fixture();
  const a=extractPinnedReplayReadout(Battle,raw);
  const newer={...raw,seed:[2,2,3,4]};
  // Fake engine preserves identical synthetic visible values. It still
  // produces a different SHA for a distinct complete replay input.
  const b=extractPinnedReplayReadout(Battle,newer);
  assert.equal(b.status,"SYNTHETIC_ENGINE_REPLAY_READOUT");
  assert.deepEqual(a.members,b.members);
  assert.notEqual(a.battle_sha256,b.battle_sha256);
});
