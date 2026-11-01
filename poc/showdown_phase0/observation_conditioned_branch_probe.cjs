"use strict";
// Phase 0: explicit observational conditioning of independent *hypothetical*
// Showdown midbattle branches. NOT real-game reconstruction or LIVE advice.
// The current-turn request boundary and unknown RNG/history remain assumptions.
const {context} = require("./six_team_selection_probe.cjs");
const fs=require("node:fs"),path=require("node:path");
const {rows}=require("./audit.cjs");
const {assertObservation, makeCandidates} = require("./partial_observation_probe.cjs");
const {copy, serialize, pinned} = require("./worker.cjs");
const {restoreChecked, state} = require("./gate_b_probe.cjs");
const {fingerprint, stableStringify} = require("./state_compare.cjs");
const CONDITIONAL_FIELDS = ["opponent_hp_exact", "opponent_boosts", "screens",
  "screen_remaining_turns"];
const BOOSTS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];
const SCREENS = ["reflect", "lightscreen"];
const observedSource = f => ["SYNTHETIC", "GAME_OBSERVED"].includes(f.source);
const known = (obs,k) => observedSource(obs.facts[k]);
// Only one-use berries in the current Champions fixed item master are
// accepted as a "consumed" source item in this narrow experiment.
// Choice Scarf/Leftovers or a Knock Off removal is NOT an item consumption.
function observedConsumableBerry(ctx,itemId){
 const dir=path.resolve(process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,"masters"));
 const entries=rows(fs.readFileSync(path.join(dir,
   "Pokemon_Champions_MC_usable_held_items.csv"),"utf8"));
 const matches=entries.filter(row=>ctx.dex.items.get(row["英語名"]).id===itemId);
 if(matches.length!==1)throw Error("fixed Champions held item master ambiguous: "+itemId);
 return matches[0]["区分"]==="きのみ"&&matches[0]["発動/消費"]==="対戦中1回";
}
function deny(reason, obsHash, info = {}) {
 return {status:"UNSUPPORTED",reason,observation_sha256:obsHash,
   gate_B:"UNSUPPORTED",game_compatibility_verified:false,
   real_game_partial_state_restored:false,live_recommendation_permitted:false,
   ...info};
}
function condition(obs, ctx = context()) {
 // assertObservation verifies schema, allowed Champions facts, and source IDs.
 // A supplied GAME_FRAME label is not independent device proof.
 assertObservation(obs, ctx);
 const originHash = fingerprint(obs), f = obs.facts;
 const preconditions = [
  ["opponent_hp_exact", known(obs,"opponent_hp_exact")],
  ["opponent_boosts", known(obs,"opponent_boosts")],
  ["opponent_status", known(obs,"opponent_status")],
  ["opponent_current_type", known(obs,"opponent_current_type")],
  ["terrain", known(obs,"terrain")],
  ["hazards", known(obs,"hazards")],
  ["screens", known(obs,"screens")],
  ["weather", known(obs,"weather")],
  ["request_state", known(obs,"request_state")],
  ["active_opponent", known(obs,"active_opponent")]
 ];
 const missing = preconditions.filter(([k,present])=>!present).map(([k])=>k);
 if(missing.length) return deny("required current-turn facts remain UNKNOWN",originHash,
   {missing_observed_fields:missing});
 if(f.request_state.value!=="move") return deny("not a confirmed move-request boundary",
   originHash,{request_state:f.request_state.value});
 const hp=f.opponent_hp_exact.value, boosts=f.opponent_boosts.value,
  screens=f.screens.value;
 if(!Number.isInteger(hp)||hp<1) return deny("fainted or invalid HP needs switch reconstruction",
   originHash);
 if(!boosts||typeof boosts!=="object"||Array.isArray(boosts)||
    Object.keys(boosts).sort().join(",")!==BOOSTS.slice().sort().join(",")||
    BOOSTS.some(k=>!Number.isInteger(boosts[k])||boosts[k]<-6||boosts[k]>6))
   return deny("all seven observed boost stages are required",originHash);
 if(!Array.isArray(screens)||screens.length>2||new Set(screens).size!==screens.length||
    screens.some(x=>!SCREENS.includes(x)))
   return deny("screens must be an explicit supported list",originHash);
 const durations= f.screen_remaining_turns.value;
 if(screens.length&&(!known(obs,"screen_remaining_turns")||
   !durations||typeof durations!=="object"||Array.isArray(durations)||
   Object.keys(durations).sort().join(",")!==screens.slice().sort().join(",")||
   screens.some(k=>!Number.isInteger(durations[k])||durations[k]<1||durations[k]>8)))
   return deny("active screen requires independently observed remaining turns",
     originHash,{missing_observed_fields:["screen_remaining_turns"]});
 if(screens.length===0 && known(obs,"screen_remaining_turns")&&
    stableStringify(durations)!=="{}")
   return deny("screen duration contradicts empty screen list",originHash);
 // Exclude weather effects rather than inventing weather/turn-history.
 if(f.weather.value!=="") return deny("nonempty weather requires duration/history reconstruction",originHash);
 // A fresh selected-turn Battle has no terrain or hazards. These must be
 // explicitly observed as absent, not silently assumed absent when UNKNOWN.
 if(f.terrain.value!=="") return deny("observed terrain is not represented in conditioned Battle",originHash);
 if(!Array.isArray(f.hazards.value)||f.hazards.value.length)
   return deny("observed hazards are not represented in conditioned Battle",originHash);
 // Only the unchanged current form/type is supported. The frozen Champions
 // master and the pinned Dex were cross-checked during context() construction.
 // A known changed type MUST NOT be ignored by makeCandidates().
 const currentForm=ctx.dex.species.get(f.opponent_form.value);
 const reportedTypes=f.opponent_current_type.value;
 if(!known(obs,"opponent_form")||!currentForm.exists||
    !Array.isArray(reportedTypes)||reportedTypes.length!==currentForm.types.length||
    !reportedTypes.every((type,index)=>type===currentForm.types[index]))
   return deny("observed current type differs from the pinned Champions base form or is malformed",
     originHash);
 // Exclude observed effects that this narrow probe cannot reconstruct.
 for(const k of ["effect_remaining_turns","opponent_mega_history",
   "choice_lock","action_history"]){
   if(known(obs,k))return deny("known hidden-history field not represented: "+k,originHash);
 }
 if(!["",null].includes(f.opponent_status.value)&&known(obs,"opponent_status"))
   return deny("opponent status duration/history not reconstructable here",originHash);
 if(known(obs,"opponent_mega_state")&&f.opponent_mega_state.value===true)
   return deny("Mega state requires separate current-form/history reconstruction",originHash);
 if(known(obs,"pending_choice")||known(obs,"unresolved_queue"))
   return deny("queued actions cannot be overwritten",originHash);
 // A confirmed consumed item and a confirmed current empty item jointly
 // constrain a *hypothetical* source held item. This is not proof that the
 // Showdown item-use event, HP restoration, or lastItem history occurred.
 const consumedKnown=known(obs,"opponent_consumed_item");
 const currentItemKnown=known(obs,"opponent_item");
 if(consumedKnown&&(!currentItemKnown||f.opponent_item.value!==""))
   return deny("consumed item requires observed empty current item",originHash);
 const consumedId=consumedKnown ?
   ctx.dex.items.get(f.opponent_consumed_item.value).id : null;
 if(consumedKnown&&(!consumedId||!ctx.allowed.availableItems.has(consumedId)))
   return deny("consumed item is absent from Champions held-item master",originHash);
 if(consumedKnown&&!observedConsumableBerry(ctx,consumedId))
   return deny("only consumed one-use Berries supported; not item loss/removal",originHash);

 // Build four legal, fixed hypothetical opponent sets WITHOUT silently treating
 // the current HP/boosts/screens as the starting-turn full snapshot.
 const hypotheticalObs=structuredClone(obs);
 for(const k of CONDITIONAL_FIELDS) hypotheticalObs.facts[k]={source:"UNKNOWN"};
 // The same observed "now empty" item would otherwise exclude every
 // candidate whose ORIGINAL held item was later consumed.
 if(consumedKnown){
   hypotheticalObs.facts.opponent_item={source:"UNKNOWN"};
   hypotheticalObs.facts.opponent_consumed_item={source:"UNKNOWN"};
 }
 const generated=makeCandidates(hypotheticalObs,ctx);
 const candidates=[],rejected=[...generated.rejected];
 for(const c of generated.candidates){
  // makeCandidates' base state and the original observation are never mutated.
  const baseHash=fingerprint(state(c.battle));
  const battle=copy(c.battle),active=battle.p2.active[0];
  const originalItem=active.item;
  if(consumedKnown&&originalItem!==consumedId){
   rejected.push({id:c.id,reason:"hypothetical original held item differs from observed consumed item"});
   continue;
  }
  if(hp>active.maxhp){
   rejected.push({id:c.id,reason:"observed HP exceeds hypothetical max HP"});
   continue;
  }
  // Exact observed values only; all other hidden properties still belong to
  // distinct hypotheses and cannot be promoted into observations.
  active.hp=hp;
  for(const k of BOOSTS)active.boosts[k]=boosts[k];
  if(consumedKnown){
    // Only the CURRENT item absence is observed. Do NOT fake eatItem/useItem,
    // the consumption effect, lastItem, or original item-use event.
    if(!active.setItem("")||active.item!==""||active.itemState?.id)
      throw Error("could not remove hypothetical held item without simulated use");
  }
  let supported=true;
  for(const name of screens){
   if(!battle.p2.addSideCondition(name,active)){
     supported=false;break;
   }
   const side=battle.p2.sideConditions[name];
   if(!side||!Number.isInteger(side.duration)){
     supported=false;break;
   }
   side.duration=durations[name];
  }
  if(!supported){
   rejected.push({id:c.id,reason:"screen effect cannot be represented"});continue;
  }
  const digest=fingerprint(state(battle));
  const restored=restoreChecked(serialize(battle),digest);
  if(fingerprint(state(restored))!==digest)
    throw Error("conditioned synthetic Battle failed serialized round trip");
  const post=copy(restored);
  post.makeChoices("move 1","move 1");
  const continuationHash=fingerprint(state(post));
  if(fingerprint(state(c.battle))!==baseHash)
    throw Error("conditioning changed an unconditioned hypothesis");
  candidates.push({id:c.id,base_sha256:baseHash,conditioned_sha256:digest,
   continuation_sha256:continuationHash,
   conditioned_hp:active.hp,conditioned_boosts:{...active.boosts},
   conditioned_screen_ids:Object.keys(battle.p2.sideConditions).sort(),
   conditioned_item:active.item,original_hypothesis_item:originalItem,
   consumed_item_observation:consumedKnown?consumedId:null,
   item_history_scope:consumedKnown?
     "CONDITIONED_EMPTY_ITEM_ONLY_NO_CONSUMPTION_EFFECT_OR_LOG_REPLAY":
     "NO_CONSUMPTION_EVENT_RECONSTRUCTED",
   hidden_set_origin:"FIXED_LEGAL_HYPOTHESIS_NOT_GAME_OBSERVED",
   conditional_result_scope:"ONE_ASSUMED_MOVE_BOUNDARY_AND_SEEDED_SIMULATOR_RNG"});
 }
 if(fingerprint(obs)!==originHash)throw Error("conditioning modified source observation");
 if(!candidates.length)return deny("no supported legal hypothesis fits conditioned facts",
  originHash,{rejected});
 if(new Set(candidates.map(c=>c.conditioned_sha256)).size!==candidates.length)
   throw Error("hypothetical states unexpectedly collapsed");
 return {schema:"phase0-observation-conditioned-branches-v1",
  pinned_showdown_sha:pinned,format:"gen9championsbssregmc",
  observation_sha256:originHash,source_origin:obs.origin,
  status:"PROVISIONAL_CONDITIONAL_SYNTHETIC",
  gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
  game_compatibility_verified:false,real_game_partial_state_restored:false,
  live_recommendation_permitted:false,
  admitted_evidence_fields:["opponent_hp_exact","opponent_boosts","screens",
    ...(screens.length?["screen_remaining_turns"]:[]),
    "opponent_current_type","terrain","hazards",
    ...(consumedKnown?["opponent_item","opponent_consumed_item"]:[])],
  unrecovered_hidden_fields:["opponent_revealed_moves","opponent_remaining_pp",
    ...(consumedKnown?[]:["opponent_item"]),
    "opponent_ability","opponent_ap","opponent_mega_history",
    "unresolved_queue","internal_rng","action_history",
    "effect_remaining_turns","own_side_unknown_history"],
  assumption:"fixed registered teams, other hidden facts, past consumption effects and move boundary are hypotheses",
  candidate_count:candidates.length,unevaluated_candidate_count:generated.unevaluated_candidate_count,
  candidates,rejected,not_a_unique_true_state:true};
}
module.exports={condition,BOOSTS};
