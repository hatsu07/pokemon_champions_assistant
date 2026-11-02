"use strict";

// Isolated Phase 2: engine-issued singles p2 ChoiceRequest and detached-copy
// validation. This is NOT a source of Champions observations or a general
// Showdown legality oracle. It only certifies the *simple request grammar*
// explicitly admitted below. All other states fail closed.
const crypto = require("node:crypto");

function stable(value) {
  if (Array.isArray(value)) return "["+value.map(stable).join(",")+"]";
  if (value && typeof value === "object") return "{"+
    Object.keys(value).sort().map(k=>JSON.stringify(k)+":"+stable(value[k])).join(",")+"}";
  return JSON.stringify(value);
}
function digest(value) {
  return crypto.createHash("sha256").update(stable(value)).digest("hex");
}
function reject(reason) {return {status:"NO_CHOICES",reason,commands:[],exhaustive_legal_choices_verified:false};}
function condition(raw) {
  // "0 fnt" has no encoded maximum HP. Its zero HP must still match the
  // deserialized Pokemon; compare maxhp when the request carries a number.
  if (raw==="0 fnt") return {hp:0,maxhp:null};
  if (typeof raw!=="string") return null;
  const m=/^([0-9]{1,9})\/([0-9]{1,9})(?: ([a-z]+))?$/.exec(raw);
  if (!m) return null;
  const hp=Number(m[1]),maxhp=Number(m[2]),status=m[3];
  if (maxhp<1 || hp>maxhp || (status==="fnt" && hp!==0) ||
      (hp===0 && status && status!=="fnt")) return null;
  return {hp,maxhp};
}
const COMPLEX = [
  "maybeDisabled","maybeLocked","maybeTrapped",
  "canMegaEvo","canMegaEvoX","canMegaEvoY","canUltraBurst",
  "canZMove","canDynamax","maxMoves","canTerastallize",
];
const MOVE_KEYS = new Set(["moves","trapped",...COMPLEX]);
const ROOT_KEYS = new Set(["active","forceSwitch","side","noCancel","update"]);
const SIDE_KEYS = new Set(["id","name","pokemon","noCancel"]);
const POKEMON_KEYS = new Set([
  "ident","details","condition","active","stats","moves","baseAbility",
  "item","pokeball","ability","commanding","reviving","teraType","terastallized",
]);
const SLOT_KEYS = new Set(["move","id","pp","maxpp","target","disabled","disabledSource"]);
function onlyKeys(obj,known) {
  return obj && typeof obj==="object" && !Array.isArray(obj) &&
    Object.keys(obj).every(key=>known.has(key));
}

function requestCommands(b) {
  if (!b || b.format?.mod!=="champions" ||
      b.gameType!=="singles" || b.activePerHalf!==1 ||
      !["move","switch"].includes(b.requestState) ||
      !Array.isArray(b.sides) || b.sides.length!==2) {
    return reject("ENGINE_P2_STATE_UNSUPPORTED");
  }
  const side=b.sides[1], req=side?.activeRequest;
  if (!side || side.id!=="p2" || side.requestState!==b.requestState ||
      !req || !onlyKeys(req,ROOT_KEYS) || req.wait || req.teamPreview ||
      req.noCancel || req.update ||
      !onlyKeys(req.side,SIDE_KEYS) || req.side.id!=="p2" ||
      req.side.noCancel || !Array.isArray(req.side.pokemon) ||
      req.side.pokemon.length!==3 || !Array.isArray(side.pokemon) ||
      side.pokemon.length!==3 || !Array.isArray(side.active) ||
      side.active.length!==1 || !side.active[0] ||
      typeof side.choose!=="function" || typeof side.isChoiceDone!=="function" ||
      !side.choice || side.choice.cantUndo ||
      !Array.isArray(side.choice.actions) || side.choice.actions.length!==0) {
    return reject("ENGINE_P2_REQUEST_INCOMPLETE");
  }
  const active=[],bench=[];
  for (let i=0;i<3;i++) {
    const p=req.side.pokemon[i], actual=side.pokemon[i];
    if (!onlyKeys(p,POKEMON_KEYS) || typeof p.active!=="boolean" ||
        !actual || typeof actual.fainted!=="boolean") {
      return reject("ENGINE_P2_ROSTER_INVALID");
    }
    const health=condition(p.condition);
    if (health===null ||
        !Number.isSafeInteger(actual.hp) || actual.hp<0 ||
        !Number.isSafeInteger(actual.maxhp) || actual.maxhp<1 ||
        actual.hp>actual.maxhp ||
        health.hp!==actual.hp ||
        (health.maxhp!==null && health.maxhp!==actual.maxhp) ||
        (actual.hp===0)!==actual.fainted ||
        p.active!==(actual===side.active[0]) ||
        !!p.commanding || !!p.reviving || !!p.terastallized) {
      return reject("ENGINE_P2_ROSTER_INVALID");
    }
    if (p.active) active.push({slot:i+1,hp:health.hp,actual});
    else if (health.hp>0) bench.push(i+1);
  }
  if (active.length!==1) return reject("ENGINE_P2_ROSTER_INVALID");
  let commands=[];
  if (b.requestState==="switch") {
    if (!Array.isArray(req.forceSwitch) || req.forceSwitch.length!==1 ||
        req.forceSwitch[0]!==true || "active" in req ||
        !active[0].actual.switchFlag ||
        side.choice.forcedSwitchesLeft !== 1) {
      return reject("ENGINE_P2_COMPLEX_SWITCH_REQUEST");
    }
    commands=bench.map(i=>"switch "+i);
  } else {
    if ("forceSwitch" in req || !Array.isArray(req.active) ||
        req.active.length!==1 || !onlyKeys(req.active[0],MOVE_KEYS) ||
        active[0].hp===0 || active[0].actual.switchFlag) {
      return reject("ENGINE_P2_COMPLEX_MOVE_REQUEST");
    }
    const a=req.active[0];
    if (COMPLEX.some(k=>k in a && a[k]!==false && a[k]!==null) ||
        (a.trapped!==undefined && typeof a.trapped!=="boolean") ||
        !Array.isArray(a.moves) || !a.moves.length || a.moves.length>4) {
      return reject("ENGINE_P2_COMPLEX_MOVE_REQUEST");
    }
    const ids=new Set();
    for (let i=0;i<a.moves.length;i++) {
      const m=a.moves[i];
      if (!onlyKeys(m,SLOT_KEYS) || typeof m.id!=="string" ||
          !/^[a-z0-9]+$/.test(m.id) || ids.has(m.id) ||
          !Number.isSafeInteger(m.pp) || m.pp<0 ||
          (m.disabled!==undefined && m.disabled!==false && m.disabled!==true) ||
          (!m.disabled && m.pp===0)) {
        return reject("ENGINE_P2_MOVE_SLOTS_INVALID");
      }
      ids.add(m.id);
      if (!m.disabled) commands.push("move "+(i+1));
    }
    if (!commands.length) return reject("ENGINE_P2_NO_SIMPLE_MOVE");
    if (!a.trapped) commands.push(...bench.map(i=>"switch "+i));
  }
  if (!commands.length || commands.length>24 || new Set(commands).size!==commands.length)
    return reject("ENGINE_P2_CHOICE_SET_EMPTY_OR_OVERSIZED");
  return {status:"SIMPLE_CANDIDATES",reason:"OK",commands,
    request:JSON.parse(JSON.stringify(req))};
}

function enumerateEngineP2(Battle, battleJson) {
  try {
    if (!Battle || typeof Battle.fromJSON!=="function" ||
        !battleJson || typeof battleJson!=="object" || Array.isArray(battleJson))
      return reject("ENGINE_P2_INPUT_INVALID");
    const before=digest(battleJson);
    // The actual request comes exclusively from this engine-deserialized
    // Battle, not from an untrusted supplied ChoiceRequest JSON.
    const b=Battle.fromJSON(JSON.parse(JSON.stringify(battleJson)));
    const parsed=requestCommands(b);
    if (parsed.status!=="SIMPLE_CANDIDATES") return parsed;
    const reqSha=digest(parsed.request);
    for (const cmd of parsed.commands) {
      // Each choice uses a clean copy of the *same* full Battle/RNG state.
      const clone=Battle.fromJSON(JSON.parse(JSON.stringify(battleJson)));
      if (digest(clone.sides?.[1]?.activeRequest)!==reqSha ||
          clone.requestState!==b.requestState ||
          clone.sides?.[1]?.requestState!==b.requestState ||
          typeof clone.sides?.[1]?.choose!=="function" ||
          typeof clone.sides?.[1]?.isChoiceDone!=="function") {
        return reject("ENGINE_P2_REQUEST_CHANGED_ON_CLONE");
      }
      let chosen=false;
      try {chosen=clone.sides[1].choose(cmd)===true;}catch (_e){return reject("ENGINE_P2_CHOICE_REJECTED");}
      if (!chosen || clone.sides[1].choice?.error ||
          clone.sides[1].choice?.cantUndo ||
          !clone.sides[1].isChoiceDone() ||
          clone.sides[1].choice?.actions?.length!==1) {
        return reject("ENGINE_P2_CHOICE_REJECTED");
      }
      const action=clone.sides[1].choice.actions[0];
      if (!action ||
          (cmd.startsWith("move ") && action.choice!=="move") ||
          (cmd.startsWith("switch ") &&
            !["switch","instaswitch"].includes(action.choice))) {
        return reject("ENGINE_P2_ACTION_MISMATCH");
      }
    }
    if (digest(battleJson)!==before) return reject("ENGINE_P2_INPUT_MUTATED");
    return {status:"ENGINE_CHECKED_SIMPLE_CHOICES",
      reason:"ENGINE_REQUEST_AND_EACH_CHOICE_CHECKED",
      commands:parsed.commands,request_sha256:reqSha,
      battle_sha256:before,
      // This flag intentionally stays FALSE: successful validation of the
      // supported request grammar cannot independently prove that all
      // engine/game mechanics and hidden response variants were modeled.
      exhaustive_legal_choices_verified:false,
      scope:"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY"};
  } catch (_e) {
    return reject("ENGINE_P2_UNAVAILABLE_OR_INVALID");
  }
}
module.exports={enumerateEngineP2};
