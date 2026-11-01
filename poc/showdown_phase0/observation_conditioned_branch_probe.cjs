"use strict";
// Phase 0: explicit observational conditioning of independent *hypothetical*
// Showdown midbattle branches. NOT real-game reconstruction or LIVE advice.
// The current-turn request boundary and unknown RNG/history remain assumptions.
const {context} = require("./six_team_selection_probe.cjs");
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
 if(!["",null].includes(f.opponent_status.value)&&known(obs,"opponent_status"))
   return deny("opponent status duration/history not reconstructable here",originHash);
 if(known(obs,"opponent_mega_state")&&f.opponent_mega_state.value===true)
   return deny("Mega state requires separate current-form/history reconstruction",originHash);
 if(known(obs,"pending_choice")||known(obs,"unresolved_queue"))
   return deny("queued actions cannot be overwritten",originHash);

 // Build four legal, fixed hypothetical opponent sets WITHOUT silently treating
 // the current HP/boosts/screens as the starting-turn full snapshot.
 const hypotheticalObs=structuredClone(obs);
 for(const k of CONDITIONAL_FIELDS) hypotheticalObs.facts[k]={source:"UNKNOWN"};
 const generated=makeCandidates(hypotheticalObs,ctx);
 const candidates=[],rejected=[...generated.rejected];
 for(const c of generated.candidates){
  // makeCandidates' base state and the original observation are never mutated.
  const baseHash=fingerprint(state(c.battle));
  const battle=copy(c.battle),active=battle.p2.active[0];
  if(hp>active.maxhp){
   rejected.push({id:c.id,reason:"observed HP exceeds hypothetical max HP"});
   continue;
  }
  // Exact observed values only; all other hidden properties still belong to
  // distinct hypotheses and cannot be promoted into observations.
  active.hp=hp;
  for(const k of BOOSTS)active.boosts[k]=boosts[k];
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
    ...(screens.length?["screen_remaining_turns"]:[])],
  unrecovered_hidden_fields:["opponent_revealed_moves","opponent_remaining_pp",
    "opponent_item","opponent_ability","opponent_ap","opponent_mega_history",
    "unresolved_queue","internal_rng","action_history"],
  assumption:"fixed registered teams, other hidden facts and move boundary are hypotheses",
  candidate_count:candidates.length,unevaluated_candidate_count:generated.unevaluated_candidate_count,
  candidates,rejected,not_a_unique_true_state:true};
}
module.exports={condition,BOOSTS};
