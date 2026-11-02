"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {enumerateEngineP2}=require("./p2_engine_choices.cjs");

const base=()=>({
  format:"champions",gameType:"singles",requestState:"move",
  hp:[100,70,0],trapped:false,flags:{},reject:"",
});
function fakeBattle(){
  return {fromJSON(raw){
    const data=JSON.parse(JSON.stringify(raw));
    const reqState=data.requestState;
    const pokemon=data.hp.map((hp,i)=>({
      hp,maxhp:data.maxhp?.[i] ?? 100,
      fainted:hp===0,switchFlag:i===0&&reqState==="switch",
      canMegaEvo:i===0&&data.movePatch?.canMegaEvo===true?"Garchomp-Mega":null
    }));
    const active=pokemon[0];
    const request={
      side:{id:"p2",pokemon:data.hp.map((hp,i)=>({
        condition:data.requestConditions?.[i] ??
          (hp===0?"0 fnt":hp+"/"+(data.maxhp?.[i] ?? 100)),
        active:i===0
      }))},
      ...(reqState==="switch"?{forceSwitch:[true]}:{
        active:[{
          moves:[
            {id:"extremespeed",pp:5,disabled:false},
            {id:"earthquake",pp:0,disabled:true},
            {id:"dragonclaw",pp:9,disabled:false},
          ],trapped:data.trapped,...data.flags
        }]
      })
    };
    if(data.forge) data.forge(request,pokemon);
    // Fakes may inject serialized structural fields, not inferred mechanics.
    if(data.requestPatch)Object.assign(request,data.requestPatch);
    if(data.movePatch)Object.assign(request.active[0],data.movePatch);
    const side={id:"p2",requestState:reqState,activeRequest:request,
      pokemon,active:[active],
      choice:{cantUndo:false,actions:[],forcedSwitchesLeft:reqState==="switch"?1:0,error:""},
      choose(command){
        const mega=command.endsWith(" mega");
        const ordinary=mega?command.slice(0,-5):command;
        const legal=reqState==="switch"?
          data.hp.map((hp,i)=>hp>0&&i!==0?"switch "+(i+1):"").filter(Boolean):
          ["move 1","move 3",...(!data.trapped&&data.hp[1]>0?["switch 2"]:[])];
        if(!legal.includes(ordinary)||(mega&&request.active?.[0]?.canMegaEvo!==true)||
            command===data.reject){
          this.choice.error="invalid";return false;
        }
        const move=/^move ([1-4])$/.exec(ordinary);
        const sw=/^switch ([1-3])$/.exec(ordinary);
        const action=move?{
          choice:"move",moveSlot:Number(move[1])-1,
          moveid:request.active[0].moves[Number(move[1])-1].id,mega
        }:{
          choice:reqState==="switch"?"instaswitch":"switch",
          target:{position:Number(sw[1])-1}
        };
        if(data.actionPatch)Object.assign(action,data.actionPatch);
        this.choice.actions=[action];return true;
      },
      isChoiceDone(){return this.choice.actions.length===1;}
    };
    return {format:{mod:data.format},gameType:data.gameType,activePerHalf:1,
      requestState:reqState,sides:[{id:"p1"},side]};
  }};
}
test("actual engine-issued simple request checks each choice on a fresh clone",()=>{
  let calls=0;
  const underlying=fakeBattle();
  const B={fromJSON(data){calls++;return underlying.fromJSON(data);}};
  const r=enumerateEngineP2(B,base());
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES");
  assert.deepEqual(r.commands,["move 1","move 3","switch 2"]);
  assert.equal(r.scope,"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY");
  assert.equal(r.exhaustive_legal_choices_verified,false);
  assert.equal(calls,r.commands.length+1);
});
test("fainted active forced switch is checked but cannot attack",()=>{
  const b=base();b.requestState="switch";b.hp=[0,70,0];
  assert.deepEqual(enumerateEngineP2(fakeBattle(),b).commands,["switch 2"]);
  const dead=base();dead.hp=[0,70,0];
  assert.equal(enumerateEngineP2(fakeBattle(),dead).status,"NO_CHOICES");
});
test("unknown or form operations are not incorrectly certified exhaustive",()=>{
  for(const movePatch of [
    {maybeLocked:true},{maybeTrapped:true},
    {canTerastallize:"Flying"},{maxMoves:{maxMoves:[]}},
    {moves:[{id:"custom",pp:0,disabled:false}]},
  ]){
    const b=base();b.movePatch=movePatch;
    const r=enumerateEngineP2(fakeBattle(),b);
    assert.equal(r.status,"NO_CHOICES");assert.deepEqual(r.commands,[]);
  }
});
test("one rejected engine choice blocks the entire list",()=>{
  const b=base();b.reject="switch 2";
  const r=enumerateEngineP2(fakeBattle(),b);
  assert.equal(r.status,"NO_CHOICES");
  assert.equal(r.reason,"ENGINE_P2_CHOICE_REJECTED");
  assert.deepEqual(r.commands,[]);
});
test("wrong engine format, mode and mismatch are unsupported",()=>{
  for(const k of [
    {format:"gen9"},{gameType:"doubles"},{requestState:"teampreview"},
    {requestPatch:{wait:true}},{movePatch:{trapped:"unknown"}},
  ]){
    const b={...base(),...k};
    const r=enumerateEngineP2(fakeBattle(),b);
    assert.equal(r.status,"NO_CHOICES");
  }
});
test("incorrect p2 active/HP or malformed choices cannot claim success",()=>{
  for(const patch of [
    {side:{id:"p1",pokemon:[]}},
    {forceSwitch:[true]},
    {active:[{moves:[]}]},
  ]){
    const b=base();b.requestPatch=patch;
    assert.equal(enumerateEngineP2(fakeBattle(),b).status,"NO_CHOICES");
  }
  const b=base();b.hp=[100,0,0];
  const good=enumerateEngineP2(fakeBattle(),b);
  assert.deepEqual(good.commands,["move 1","move 3"]);
});
test("request HP must match Battle HP even when both say alive",()=>{
  const b=base();b.hp=[25,70,0];
  b.requestConditions=["100/100","70/100","0 fnt"];
  const r=enumerateEngineP2(fakeBattle(),b);
  assert.equal(r.status,"NO_CHOICES");
  assert.equal(r.reason,"ENGINE_P2_ROSTER_INVALID");
  assert.deepEqual(r.commands,[]);
  assert.equal(r.exhaustive_legal_choices_verified,false);
});
test("request maxHP must match Battle maxHP independently of current HP",()=>{
  for (const b of [
    {...base(),requestConditions:["100/200","70/100","0 fnt"]},
    {...base(),maxhp:[200,100,100],
      requestConditions:["100/100","70/100","0 fnt"]},
  ]) {
    const r=enumerateEngineP2(fakeBattle(),b);
    assert.equal(r.status,"NO_CHOICES");
    assert.equal(r.reason,"ENGINE_P2_ROSTER_INVALID");
    assert.deepEqual(r.commands,[]);
  }
});
test("matching HP and maxHP, fainted active replacement remain accepted",()=>{
  const b=base();b.maxhp=[200,100,100];b.hp=[125,70,0];
  const r=enumerateEngineP2(fakeBattle(),b);
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES");
  assert.deepEqual(r.commands,["move 1","move 3","switch 2"]);
  const fainted=base();fainted.requestState="switch";
  fainted.hp=[0,70,0];fainted.maxhp=[200,100,100];
  assert.deepEqual(enumerateEngineP2(fakeBattle(),fainted).commands,["switch 2"]);
});
test("real-style noCancel only allowed for single forced replacement",()=>{
  const forced=base();
  forced.requestState="switch";
  forced.hp=[0,70,0];
  forced.requestPatch={noCancel:true};
  const r=enumerateEngineP2(fakeBattle(),forced);
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.deepEqual(r.commands,["switch 2"]);
  assert.equal(r.exhaustive_legal_choices_verified,false);
  const moving=base();
  moving.requestPatch={noCancel:true};
  assert.equal(enumerateEngineP2(fakeBattle(),moving).status,"NO_CHOICES");
  const malformed=base();
  malformed.requestState="switch";
  malformed.hp=[0,70,0];
  malformed.requestPatch={noCancel:"true"};
  assert.equal(enumerateEngineP2(fakeBattle(),malformed).status,"NO_CHOICES");
});
test("stale/untrusted engine missing Battle.fromJSON is NO_CHOICES",()=>{
  assert.equal(enumerateEngineP2({},base()).status,"NO_CHOICES");
  assert.equal(enumerateEngineP2({fromJSON(){throw Error("bad")}},base()).status,"NO_CHOICES");
});

test("single Mega adds distinct checked normal and mega choices",()=>{
  const b=base();b.movePatch={canMegaEvo:true};
  const result=enumerateEngineP2(fakeBattle(),b);
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",result.reason);
  assert.deepEqual(result.commands,[
    "move 1","move 1 mega","move 3","move 3 mega","switch 2"
  ]);
  assert.equal(result.scope,"PINNED_SHOWDOWN_SINGLE_MEGA_SINGLES_ONLY");
  assert.equal(result.exhaustive_legal_choices_verified,false);
});
test("fake Mega and unrelated action flags must fail closed",()=>{
  for(const patch of [
    {canMegaEvo:"true"},{canMegaEvo:1},
    {canMegaEvoX:true},{canMegaEvoY:true},{canUltraBurst:true},
    {canZMove:[{}]},{canTerastallize:"Flying"},
  ]){
    const b=base();b.movePatch=patch;
    assert.equal(enumerateEngineP2(fakeBattle(),b).status,"NO_CHOICES");
  }
  const forged=base();forged.requestPatch={active:[{
    moves:[{id:"extremespeed",pp:5,disabled:false}],canMegaEvo:true
  }]};
  assert.equal(enumerateEngineP2(fakeBattle(),forged).status,"NO_CHOICES");
});
test("engine acceptance alone cannot mask a different move or lost Mega flag",()=>{
  for(const actionPatch of [
    {moveSlot:2},{moveid:"dragonclaw"},{mega:true},
    {zmove:true},{maxMove:"maxquake"}
  ]){
    const b=base();b.actionPatch=actionPatch;
    const result=enumerateEngineP2(fakeBattle(),b);
    assert.equal(result.status,"NO_CHOICES",JSON.stringify(actionPatch));
    assert.equal(result.reason,"ENGINE_P2_ACTION_MISMATCH");
  }
  const b=base();b.movePatch={canMegaEvo:true};
  b.actionPatch={mega:false};
  assert.equal(enumerateEngineP2(fakeBattle(),b).status,"NO_CHOICES");
  const sw=base();sw.requestState="switch";sw.hp=[0,70,0];
  sw.actionPatch={target:{position:2}};
  assert.equal(enumerateEngineP2(fakeBattle(),sw).reason,"ENGINE_P2_ACTION_MISMATCH");
});
