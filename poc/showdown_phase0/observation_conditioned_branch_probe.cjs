"use strict";
// Phase 0: explicit observational conditioning of independent *hypothetical*
// Showdown midbattle branches. NOT real-game reconstruction or LIVE advice.
// The current-turn request boundary and unknown RNG/history remain assumptions.
const {context} = require("./six_team_selection_probe.cjs");
const fs=require("node:fs"),path=require("node:path");
const {rows}=require("./audit.cjs");
const {moveKey}=require("./champions_move_key.cjs");
const {assertObservation, makeCandidates} = require("./partial_observation_probe.cjs");
const {copy, serialize, pinned} = require("./worker.cjs");
const {restoreChecked, state} = require("./gate_b_probe.cjs");
const {fingerprint, stableStringify} = require("./state_compare.cjs");
const combined=require("./gate_b_four_mechanisms.cjs");
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
 // Nonempty weather, terrain and one opponent-side hazard are handled only
 // when their current-frame evidence and exact applicable turns are available.
 // Unsupported statuses/other effects remain fail-closed in the combined gate.
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
 if(!["",null].includes(f.opponent_status.value)&&known(obs,"opponent_status"))
   return deny("opponent status duration/history not reconstructable here",originHash);
 // A legacy scalar PP is not tied to any specific move or point in time.
 // Only an atomic move-scoped reading from the SAME current-frame evidence
 // as the move request, active opponent, HP and boosts can condition a Battle.
 // Frame ID/epoch/revision are reported provenance, not device attestation.
 const ppKnown=known(obs,"opponent_remaining_pp");
 const revealedMovesKnown=known(obs,"opponent_revealed_moves");
 const revealedMoves=revealedMovesKnown ? f.opponent_revealed_moves.value : null;
 let ppReading=null,ppMove=null,ppSnapshot=null;
 if(ppKnown){
   ppReading=f.opponent_remaining_pp.value;
   if(!ppReading||typeof ppReading!=="object"||Array.isArray(ppReading))
     return deny("unbound scalar PP lacks an observed move and current snapshot",originHash);
   ppMove=moveKey(ppReading.move_id);
   if(!Array.isArray(revealedMoves)||revealedMoves.length!==1||
     moveKey(revealedMoves[0])!==ppMove)
     return deny("atomic PP move conflicts with exactly one revealed move",originHash);
   const refs=f.opponent_remaining_pp.evidence_ids;
   const evidence=refs?.length===1 ?
     obs.evidence.find(e=>e.id===refs[0]) : null;
   const sameFrame=["request_state","active_opponent",
     "opponent_hp_exact","opponent_boosts"].every(key=>
       known(obs,key)&&obs.facts[key].evidence_ids?.includes(evidence?.id));
   if(!evidence||typeof evidence.match_id!=="string"||!evidence.match_id||
     !Number.isInteger(evidence.turn_epoch)||evidence.turn_epoch<0||
     !Number.isInteger(evidence.state_revision)||evidence.state_revision<0||
     evidence.turn_epoch!==ppReading.turn_epoch||
     evidence.state_revision!==ppReading.state_revision||!sameFrame)
     return deny("PP move/snapshot lacks evidence matching the current request and state revision",originHash);
   ppSnapshot={evidence_id:evidence.id,match_id:evidence.match_id,
     turn_epoch:evidence.turn_epoch,state_revision:evidence.state_revision};
   if(ppReading.remaining_pp===0)
     return deny("zero PP continuation requires unmodeled move availability",originHash);
 }
 // Bind atomic PP before checking other current-state fields so stale PP
 // is never masked by a separate invalid synthetic hazard baseline.
 const plan=combined.inspect(obs,ctx);
 if(plan.error)return deny(plan.error,originHash);
 // A confirmed consumed item and a confirmed current empty item jointly
 // constrain a *hypothetical* source held item. This is not proof that the
 // Showdown item-use event, HP restoration, or lastItem history occurred.
 const consumedKnown=known(obs,"opponent_consumed_item");
 const currentItemKnown=known(obs,"opponent_item");
 // An empty currently held item without verified consumption/removal history
 // must not return a Battle that still holds its original registered item.
 if(currentItemKnown&&f.opponent_item.value===""&&!consumedKnown&&!plan.removed)
   return deny("observed empty current item lacks supported consumption history",originHash);
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
 // Filter by the independently revealed move, but do not compare current
 // remaining PP to the fresh, full-PP registered-set hypothesis.
 if(ppKnown)hypotheticalObs.facts.opponent_remaining_pp={source:"UNKNOWN"};
 if(plan.removed){
   hypotheticalObs.facts.opponent_item={source:"UNKNOWN"};
   hypotheticalObs.facts.opponent_removed_item={source:"UNKNOWN"};
 }
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
  if(plan.removed&&originalItem!==plan.removed.item_id){
    rejected.push({id:c.id,reason:"original item mismatches observed removal"});
    continue;
  }
  // These are current-state constraints, not a replay of real past turns.
  const combinedError=combined.apply(battle,active,plan);
  if(combinedError){
    rejected.push({id:c.id,reason:combinedError});
    continue;
  }
  if(ppKnown){
    const slots=active.moveSlots,baseSlots=active.baseMoveSlots;
    if(slots?.length!==1||baseSlots?.length!==1||
       slots[0].id!==ppMove||baseSlots[0].id!==ppMove||
       slots[0].maxpp!==baseSlots[0].maxpp){
      rejected.push({id:c.id,reason:"observed PP does not identify a unique actual move slot"});
      continue;
    }
    const observedPP=ppReading.remaining_pp;
    if(observedPP>slots[0].maxpp){
      rejected.push({id:c.id,reason:"observed PP exceeds Champions candidate move maximum"});
      continue;
    }
  }
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
  if(ppKnown){
    // Set only the already identified move's current PP. Historical move
    // events, ability-driven PP changes and previous turns are NOT replayed.
    active.moveSlots[0].pp=ppReading.remaining_pp;
    active.baseMoveSlots[0].pp=ppReading.remaining_pp;
  }
  if(consumedKnown){
    // Only the CURRENT item absence is observed. Do NOT fake eatItem/useItem,
    // the consumption effect, lastItem, or original item-use event.
    if(!active.setItem("")||active.item!==""||active.itemState?.id)
      throw Error("could not remove hypothetical held item without simulated use");
  }
  // candidate().model can copy an observed item fact without changing the
  // Battle itself. Verify the actual active item, not only model compatibility.
  if(currentItemKnown&&active.item!==f.opponent_item.value){
    rejected.push({id:c.id,reason:"actual conditioned Battle item conflicts with observed current item"});
    continue;
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
  if(!combined.matches(restored,plan))
    throw Error("restored four-mechanism Battle conflicts with observed current state");
  if(ppKnown&&(
    restored.p2.active[0].moveSlots[0]?.id!==ppMove||
    restored.p2.active[0].moveSlots[0]?.pp!==ppReading.remaining_pp||
    restored.p2.active[0].baseMoveSlots[0]?.pp!==ppReading.remaining_pp))
    throw Error("restored Battle PP conflicts with observed move and current PP");
  if(currentItemKnown&&restored.p2.active[0].item!==f.opponent_item.value)
    throw Error("restored Battle item conflicts with observed current item");
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
   conditioned_pp:ppKnown?active.moveSlots[0].pp:null,
   conditioned_pp_move:ppMove,conditioned_pp_max:ppKnown?active.moveSlots[0].maxpp:null,
   conditioned_pp_snapshot:ppSnapshot,
   pp_history_scope:ppKnown?"ATOMIC_MOVE_PP_SAME_CURRENT_SNAPSHOT_NO_MOVE_EVENT_REPLAY":
     "NO_PP_HISTORY_RECONSTRUCTED",
   conditioned_item:active.item,original_hypothesis_item:originalItem,
   conditioned_choice_lock:active.volatiles.choicelock?.move||null,
   conditioned_mega_form:plan.mega?active.species.id:null,
   conditioned_mega_ability:plan.mega?active.ability:null,
   conditioned_weather:battle.field.weather,
   conditioned_weather_turns:plan.weather?battle.field.weatherState.duration:null,
   conditioned_terrain:battle.field.terrain,
   conditioned_terrain_turns:plan.terrain?battle.field.terrainState.duration:null,
   conditioned_hazards:plan.hazards,
   hazard_observation_scope:plan.hazardObservationScope,
   removed_item_observation:plan.removed?.item_id||null,
   history_scope:"CONDITIONED_CURRENT_STATE_ONLY_NO_PRIOR_EVENT_OR_RNG_REPLAY",
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
    "opponent_current_type","terrain",
    ...(plan.hazardObservationScope==="CURRENT_OPPONENT_SIDE_AND_REVISION"?["hazards"]:[]),
    ...(consumedKnown?["opponent_item","opponent_consumed_item"]:[]),
    ...(ppKnown?["opponent_revealed_moves","opponent_remaining_pp"]:[]),
    ...plan.admitted],
  unrecovered_hidden_fields:[
    // A single revealed move never proves the opponent has no other moves.
    "opponent_unrevealed_moves",
    ...(plan.hazardObservationScope==="SYNTHETIC_BASELINE_ONLY_NOT_CURRENT_GAME_EVIDENCE"?
      ["current_hazard_absence_unverified_for_game"]:[]),
    ...(ppKnown?[]:["opponent_revealed_moves","opponent_remaining_pp"]),
    ...(consumedKnown||plan.removed?[]:["opponent_item"]),
    ...(plan.mega?[]:["opponent_ability"]),"opponent_ap",
    ...(plan.mega?[]:["opponent_mega_history"]),
    ...(plan.choice?[]:["choice_lock"]),
    ...(plan.removed?[]:["opponent_removed_item"]),
    "unresolved_queue","internal_rng","action_history",
    ...(plan.effects?[]:["effect_remaining_turns"]),
    ...(plan.mega?["mega_transition_log_not_replayed"]:[]),
    ...(plan.removed?["item_removal_log_not_replayed"]:[]),
    ...(plan.choice?["choice_lock_creation_history_not_replayed"]:[]),
    "own_side_unknown_history"],
  assumption:"fixed registered teams and unobserved effects/history, RNG, and the current move boundary remain hypotheses; changed current state does not reconstruct prior events",
  candidate_count:candidates.length,unevaluated_candidate_count:generated.unevaluated_candidate_count,
  candidates,rejected,not_a_unique_true_state:true};
}
module.exports={condition,BOOSTS};
