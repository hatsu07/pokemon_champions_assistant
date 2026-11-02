"use strict";
// Phase 0 only. Candidate sets are fixed legal fixtures, never recovered hidden game state.
const {context, validateRegistration, makeSelectedBattle} = require("./six_team_selection_probe.cjs");
const {observed, copy, serialize, pinned} = require("./worker.cjs");
const {state, restoreChecked, masterHashes, GAP_FIELDS} = require("./gate_b_probe.cjs");
const {fingerprint, stableStringify} = require("./state_compare.cjs");
const {moveKey} = require("./champions_move_key.cjs");
const {rows} = require("./audit.cjs");
const fs = require("node:fs");
const path = require("node:path");
const schema = require("./partial_observation_schema.json");
const manifest = require("./partial_observation_cases.json");

const FIELDS = schema.properties.facts.required;
const TEMPLATE_IDS = ["A", "B", "C", "D"];
const TEMPLATES = Object.freeze({
  A: {move: "Earthquake", item: "", ability: "Rough Skin", nature: "Serious", spe: 2},
  B: {move: "Dragon Claw", item: "Sitrus Berry", ability: "Sand Veil", nature: "Serious", spe: 2},
  C: {move: "Earthquake", item: "Choice Scarf", ability: "Rough Skin", nature: "Jolly", spe: 32},
  D: {move: "Earthquake", item: "Garchompite", ability: "Rough Skin", nature: "Serious", spe: 2},
});
// Only this non-Z Mega form has an explicitly checked Champions/Japanese mapping.
const MEGA_GARCHOMP_ABILITY = Object.freeze({sandforce: "すなのちから"});
function currentFormAbilityIsAllowed(ctx, form, entry, abilityId) {
  if (!form.isMega) {
    const ja = {roughskin: "さめはだ", sandveil: "すながくれ"}[abilityId];
    return Boolean(ja && entry.abilities.has(ja) &&
      Object.values(form.abilities).some(x => ctx.dex.abilities.get(x).id === abilityId));
  }
  if (form.id !== "garchompmega")
    throw Error("unsupported current Mega form ability mapping: " + form.id);
  const masterDir = path.resolve(process.env.CHAMPIONS_MASTERS_DIR || path.join(__dirname, "masters"));
  const megaRows = rows(fs.readFileSync(path.join(masterDir,
    "Pokemon_Champions_MC_usable_pokemon.csv"), "utf8"))
    .filter(row => row["ポケモン名"] === "メガガブリアス" &&
      row["区分"] === "メガシンカ" && row["全国No."] === entry.nationalNo);
  if (megaRows.length !== 1 || megaRows[0]["特性"].trim() !== "すなのちから")
    throw Error("Mega Garchomp ability mapping disagrees with Champions master");
  const ja = MEGA_GARCHOMP_ABILITY[abilityId];
  return Boolean(ja && megaRows[0]["特性"].split("/").map(x => x.trim()).includes(ja) &&
    Object.values(form.abilities).some(x => ctx.dex.abilities.get(x).id === abilityId));
}
const SELECTION = [{slots: [1, 2, 3], lead_slot: 1}, {slots: [2, 1, 3], lead_slot: 2}];
const SPECIAL = new Set(["mega", "wall", "consumed", "forced", "pending", "choice"]);
const EVENT_TYPES = new Set(["move_used", "item_revealed", "item_consumed",
  "ability_revealed", "mega_seen", "switch_seen", "boost_seen",
  "choice_lock_revealed", "action_order_seen"]);
const STATUS = ["CONSISTENT_HYPOTHESES", "CONTRADICTORY_OBSERVATION",
  "INCONCLUSIVE", "UNSUPPORTED", "SYNTHETIC_TEST_PASS"];
const LEGACY_GAP_MAP = {
  opponent_unseen_moves: ["opponent_revealed_moves"],
  opponent_remaining_pp: ["opponent_remaining_pp"],
  opponent_unrevealed_ability: ["opponent_ability"],
  opponent_unrevealed_item: ["opponent_item"],
  opponent_ability_points: ["opponent_ap"],
  internal_rng_state: ["internal_rng"],
  unobserved_effect_history: ["action_history", "opponent_mega_history", "opponent_consumed_item"],
  unresolved_action_queue: ["unresolved_queue", "pending_choice"],
};
if (GAP_FIELDS.some(k => !LEGACY_GAP_MAP[k]) ||
    Object.keys(LEGACY_GAP_MAP).length !== GAP_FIELDS.length)
  throw Error("Gate B B2 gap contract changed");
const RESTORE_GAPS = [...new Set([...Object.values(LEGACY_GAP_MAP).flat(),
  "opponent_selected", "opponent_hp_exact", "opponent_boosts",
  "opponent_mega_state", "opponent_current_type", "screen_remaining_turns",
  "effect_remaining_turns", "choice_lock", "opponent_nature",
  "opponent_removed_item"])];

function unknown() { return {source: "UNKNOWN"}; }
function synthetic(value, id) { return {source: "SYNTHETIC", value, evidence_ids: [id]}; }
function inferred(value, reason = "fixed legal fixture hypothesis") {
  return {source: "INFERRED", value, reason};
}
function assertManifest(cases = manifest.cases) {
  if (manifest.schema !== "phase0-partial-observation-cases-v1" || cases.length !== 26 ||
      new Set(cases.map(c => c.id)).size !== 26 ||
      fingerprint(cases) !== "87f13772761b15a0ff4b5f4eca1041b77ee1e721dbc11130156be5dc31581a1e")
    throw Error("partial case coverage changed");
  const mechanisms = ["unknown_opponent_move", "unknown_opponent_item",
    "unknown_opponent_ability", "unknown_opponent_nature_ap", "unknown_remaining_pp",
    "unknown_exact_opponent_hp", "known_mega_unknown_history", "unknown_choice_lock",
    "known_wall_unknown_duration", "consumed_item_unknown_history",
    "uncertain_forced_switch_request", "unknown_pending_choice", "unknown_internal_rng",
    "multiple_legal_sets_same_observation", "revealed_move_excludes_only_candidate",
    "revealed_item_excludes_only_candidate", "ability_revelation_updates_candidates",
    "mega_revelation_updates_candidates", "switch_clears_choice_lock",
    "contradictory_observed_items", "move_revelation_updates_candidates",
    "item_revelation_updates_candidates", "consumed_item_updates_candidates",
    "boost_revelation_preserves_unknowns", "action_order_only_constrains_speed",
    "candidate_cap_reports_unsearched"];
  const expected = i => i === 19 ? "CONTRADICTORY_OBSERVATION" :
    [6, 8, 9, 14, 15, 24].includes(i) ? "INCONCLUSIVE" :
    [10, 11].includes(i) ? "UNSUPPORTED" :
    [16, 17, 18, 20, 21, 22, 23].includes(i) ? "SYNTHETIC_TEST_PASS" :
    "CONSISTENT_HYPOTHESES";
  for (const [i, c] of cases.entries()) {
    if (c.id !== `PO-${String(i + 1).padStart(2, "0")}` ||
        c.mechanism !== mechanisms[i] || c.expected_status !== expected(i) ||
        !TEMPLATE_IDS.includes(c.truth) ||
        c.setup && !SPECIAL.has(c.setup) ||
        c.events?.some(e => !EVENT_TYPES.has(e.type)))
      throw Error("partial case identity or expectation changed: " + (c.id || i));
  }
}
function selectedTeams(ctx, templateId, override = null) {
  const base = TEMPLATES[templateId];
  if (!base) throw Error("unknown candidate fixture: " + templateId);
  const spec = {...base, ...override};
  const teams = ctx.registered.map(team => structuredClone(team));
  const mon = teams[1][1];
  mon.moves = [spec.move]; mon.item = spec.item; mon.ability = spec.ability;
  mon.nature = spec.nature; mon.evs.spe = spec.spe;
  for (const team of teams) {
    const result = validateRegistration(team, ctx);
    if (!result.legal) throw Error("candidate fixture not legal in both validators: " +
      templateId + "/" + JSON.stringify(result));
  }
  return teams;
}
function syntheticTruth(ctx, entry) {
  const teams = selectedTeams(ctx, entry.truth);
  const {battle} = makeSelectedBattle(teams, SELECTION);
  const mon = battle.p2.active[0];
  switch (entry.setup) {
    case "mega":
      mon.species = battle.dex.species.get("Garchomp-Mega");
      mon.isMega = true;
      break;
    case "wall":
      if (!battle.p2.addSideCondition("reflect", mon)) throw Error("synthetic wall setup failed");
      break;
    case "consumed": mon.item = ""; break;
    case "forced": mon.switchFlag = true; battle.makeRequest("switch"); break;
    case "pending":
      if (!battle.choose("p1", "move 1")) throw Error("synthetic pending choice failed");
      break;
    case "choice":
    case undefined: break;
    default: throw Error("unknown truth setup: " + entry.setup);
  }
  return battle;
}
function projectObservation(truth, ctx, entry) {
  // observed() is a synthetic projection. In particular, its opponent HP is
  // deliberately discarded because no game-screen integer HP proof exists.
  const view = observed(truth);
  const id = "fixture-" + entry.id;
  const facts = Object.fromEntries(FIELDS.map(field => [field, unknown()]));
  const known = (field, value) => { facts[field] = synthetic(value, id); };
  known("own_registered", ctx.registered[0].map(x => ctx.dex.species.get(x.species).id));
  known("opponent_registered", ctx.registered[1].map(x => ctx.dex.species.get(x.species).id));
  known("own_selected", truth.p1.pokemon.map(x => x.species.id));
  known("active_own", view.sides[0].active[0].species);
  known("active_opponent", view.sides[1].active[0].species);
  known("opponent_species", truth.p2.active[0].species.baseSpecies.toLowerCase());
  known("opponent_form", view.sides[1].active[0].species);
  known("opponent_status", view.sides[1].active[0].status);
  known("weather", view.weather);
  known("screens", view.sides[1].conditions);
  if (entry.setup === "mega") known("opponent_mega_state", true);
  if (entry.setup === "consumed") known("opponent_item", "");
  if (entry.setup !== "forced" && entry.setup !== "pending") known("request_state", view.requestState);
  return {schema: "phase0-partial-observation-v1", origin: "SYNTHETIC",
    verification: "UNVERIFIED", evidence: [{id, kind: "SYNTHETIC_FIXTURE"}], facts};
}
function assertObservation(obs, ctx) {
  if (!obs || obs.schema !== "phase0-partial-observation-v1" ||
      !["SYNTHETIC", "REAL_GAME"].includes(obs.origin) ||
      !["UNVERIFIED", "USER_VERIFIED"].includes(obs.verification) ||
      !Array.isArray(obs.evidence) || !obs.facts ||
      stableStringify(Object.keys(obs.facts).sort()) !== stableStringify([...FIELDS].sort()) ||
      Object.keys(obs).some(k => !["schema", "origin", "verification", "evidence", "facts"].includes(k)))
    throw Error("invalid partial observation schema");
  const refs = new Map();
  for (const e of obs.evidence) {
    if (!e?.id || refs.has(e.id) ||
        !["SYNTHETIC_FIXTURE", "GAME_FRAME", "GAME_LOG"].includes(e.kind))
      throw Error("invalid or duplicate evidence ID");
    refs.set(e.id, e.kind);
  }
  for (const [field, f] of Object.entries(obs.facts)) {
    if (!f || !["GAME_OBSERVED", "SYNTHETIC", "INFERRED", "UNKNOWN"].includes(f.source) ||
        Object.keys(f).some(k => !["source", "value", "evidence_ids", "reason"].includes(k)))
      throw Error("invalid fact: " + field);
    if (f.source === "UNKNOWN") {
      if (Object.hasOwn(f, "value") || f.evidence_ids || f.reason)
        throw Error("UNKNOWN cannot carry a value: " + field);
    } else if (!Object.hasOwn(f, "value")) throw Error("known or inferred fact lacks value: " + field);
    if (f.source === "INFERRED") {
      if (!f.reason || f.evidence_ids) throw Error("inference requires reason, not observed evidence: " + field);
    } else if (f.source !== "UNKNOWN") {
      if (!Array.isArray(f.evidence_ids) || !f.evidence_ids.length ||
          f.evidence_ids.some(id => !refs.has(id))) throw Error("fact evidence missing: " + field);
      if (f.source === "SYNTHETIC" &&
          (obs.origin !== "SYNTHETIC" || f.evidence_ids.some(id => refs.get(id) !== "SYNTHETIC_FIXTURE")))
        throw Error("synthetic evidence origin mismatch: " + field);
      if (f.source === "GAME_OBSERVED" &&
          (obs.origin !== "REAL_GAME" || obs.verification !== "USER_VERIFIED" ||
           f.evidence_ids.some(id => !["GAME_FRAME", "GAME_LOG"].includes(refs.get(id)))))
        throw Error("game observation lacks verified game evidence: " + field);
    }
  }
  const f = obs.facts, present = key => f[key].source !== "UNKNOWN";
  const species = present("opponent_species") && ctx.dex.species.get(f.opponent_species.value);
  const entry = species && ctx.allowed.species.get(species.id);
  if (present("opponent_species") && !entry) throw Error("opponent species absent from Champions master");
  if (entry && present("opponent_registered") &&
      (!Array.isArray(f.opponent_registered.value) ||
       !f.opponent_registered.value.includes(species.id)))
    throw Error("active opponent species absent from registered six");
  if (entry && present("opponent_selected") &&
      (!Array.isArray(f.opponent_selected.value) ||
       !f.opponent_selected.value.includes(species.id)))
    throw Error("active opponent species absent from known selected three");
  if (present("opponent_form")) {
    const form = ctx.dex.species.get(f.opponent_form.value);
    if (!form.exists || entry && ctx.dex.species.get(form.baseSpecies).id !== species.id)
      throw Error("opponent form conflicts with known species");
    if (present("opponent_mega_state") && Boolean(form.isMega) !== f.opponent_mega_state.value)
      throw Error("opponent Mega state conflicts with form");
    if (present("active_opponent") && ctx.dex.species.get(f.active_opponent.value).id !== form.id)
      throw Error("active opponent and form disagree");
  }
  if (present("opponent_revealed_moves")) {
    if (!Array.isArray(f.opponent_revealed_moves.value)) throw Error("revealed moves must be an array");
    for (const name of f.opponent_revealed_moves.value) {
      const key = moveKey(name);
      if (!key || !ctx.allowed.availableMoves.has(key) ||
          entry && (!entry.learned.has(key) ||
            !Object.hasOwn(ctx.dex.species.getLearnsetData(entry.learnsetKey)?.learnset || {}, key)))
        throw Error("revealed move absent from Champions master or learnset: " + name);
    }
  }
  if (present("opponent_item") && f.opponent_item.value !== "") {
    const item = ctx.dex.items.get(f.opponent_item.value);
    if (!ctx.allowed.availableItems.has(item.id))
      throw Error("observed item absent from Champions master");
    const owner = ctx.allowed.megaOwners.get(item.id);
    if (owner && entry && (owner !== entry.nameJa ||
        !Object.hasOwn(item.megaStone || {}, species.baseSpecies)))
      throw Error("observed mega stone wrong species");
  }
  if (present("opponent_consumed_item") &&
      !ctx.allowed.availableItems.has(ctx.dex.items.get(f.opponent_consumed_item.value).id))
    throw Error("consumed item absent from Champions master");
  if (present("opponent_ability") && entry) {
    if (f.opponent_mega_state.value === true && !present("opponent_form"))
      throw Error("observed Mega ability requires a confirmed current form");
    const id = ctx.dex.abilities.get(f.opponent_ability.value).id;
    const form = present("opponent_form") ?
      ctx.dex.species.get(f.opponent_form.value) : species;
    if (!currentFormAbilityIsAllowed(ctx, form, entry, id))
      throw Error("observed ability absent from Champions species slots");
  }
  if (present("opponent_boosts") &&
      (typeof f.opponent_boosts.value !== "object" ||
       Object.values(f.opponent_boosts.value).some(x => !Number.isInteger(x) || x < -6 || x > 6)))
    throw Error("invalid observed boost");
  if (present("opponent_remaining_pp")) {
    const pp = f.opponent_remaining_pp.value;
    const atomic = pp !== null && typeof pp === "object" && !Array.isArray(pp);
    if (atomic) {
      // This shape binds the PP to one move AND one capture revision. Legacy
      // unbound numeric PP remains valid for older partial-observation cases,
      // but must never be used for conditional Battle PP reconstruction.
      if (Object.keys(pp).sort().join(",") !==
            "move_id,remaining_pp,state_revision,turn_epoch" ||
          !Number.isInteger(pp.remaining_pp) || pp.remaining_pp < 0 ||
          !Number.isInteger(pp.turn_epoch) || pp.turn_epoch < 0 ||
          !Number.isInteger(pp.state_revision) || pp.state_revision < 0 ||
          typeof pp.move_id !== "string" || !moveKey(pp.move_id) ||
          !ctx.allowed.availableMoves.has(moveKey(pp.move_id)) ||
          entry && !entry.learned.has(moveKey(pp.move_id)))
        throw Error("invalid atomic observed PP move/revision");
    } else if (!Number.isInteger(pp) || pp < 0) {
      throw Error("invalid observed PP");
    }
  }
  if (present("opponent_hp_exact") &&
      (!Number.isInteger(f.opponent_hp_exact.value) || f.opponent_hp_exact.value < 0))
    throw Error("invalid exact HP");
  if (present("opponent_nature") && !ctx.dex.natures.get(f.opponent_nature.value).exists)
    throw Error("invalid opponent nature");
  if (present("opponent_ap")) {
    const ap = f.opponent_ap.value, stats = ["hp", "atk", "def", "spa", "spd", "spe"];
    if (!ap || stats.some(k => !Number.isInteger(ap[k]) || ap[k] < 0 || ap[k] > 32) ||
        stats.reduce((n, k) => n + ap[k], 0) > 66)
      throw Error("invalid Champions opponent AP");
  }
  return {consistent: true,
    observed_fields: FIELDS.filter(k => ["SYNTHETIC", "GAME_OBSERVED"].includes(f[k].source)),
    inferred_fields: FIELDS.filter(k => f[k].source === "INFERRED"),
    unknown_fields: FIELDS.filter(k => f[k].source === "UNKNOWN"),
    evidence_ids: [...refs.keys()],
    missing_internal_state: RESTORE_GAPS.filter(k =>
      f[k].source === "UNKNOWN" || f[k].source === "INFERRED"),
    real_game_unverified_fields: FIELDS.filter(k => f[k].source !== "GAME_OBSERVED")};
}
function candidate(ctx, id, obs) {
  const spec = TEMPLATES[id], teams = selectedTeams(ctx, id);
  const {battle} = makeSelectedBattle(teams, SELECTION);
  const active = battle.p2.active[0];
  const model = {
    own_registered: inferred(teams[0].map(x => battle.dex.species.get(x.species).id)),
    opponent_registered: inferred(teams[1].map(x => battle.dex.species.get(x.species).id)),
    own_selected: inferred(battle.p1.pokemon.map(x => x.species.id)),
    opponent_selected: inferred(battle.p2.pokemon.map(x => x.species.id)),
    active_own: inferred(battle.p1.active[0].species.id),
    opponent_species: inferred("garchomp"),
    opponent_moves: inferred([moveKey(spec.move)]),
    opponent_item: inferred(active.item), initial_item: inferred(active.item),
    opponent_ability: inferred(active.ability), opponent_nature: inferred(spec.nature),
    opponent_ap: inferred(structuredClone(teams[1][1].evs)),
    opponent_remaining_pp: inferred(active.moveSlots[0].pp),
    opponent_hp_exact: inferred(active.hp), opponent_status: inferred(active.status),
    opponent_boosts: inferred(structuredClone(active.boosts)),
    opponent_mega_state: inferred(false), choice_lock: inferred(""),
    active_opponent: inferred("garchomp"), opponent_form: inferred("garchomp"),
    weather: structuredClone(obs.facts.weather), screens: structuredClone(obs.facts.screens),
    request_state: inferred(battle.requestState),
  };
  if (obs.facts.opponent_item.source !== "UNKNOWN" && obs.facts.opponent_item.value === "")
    model.opponent_item = structuredClone(obs.facts.opponent_item);
  if (obs.facts.opponent_mega_state.value === true && active.item === "garchompite")
    model.opponent_mega_state = structuredClone(obs.facts.opponent_mega_state);
  if (obs.facts.opponent_mega_state.value === true && active.item === "garchompite")
    model.opponent_form = structuredClone(obs.facts.opponent_form);
  if (obs.facts.opponent_mega_state.value === true && active.item === "garchompite")
    model.active_opponent = structuredClone(obs.facts.active_opponent);
  if (obs.facts.opponent_mega_state.value === true && active.item === "garchompite") {
    model.previous_form_ability = structuredClone(model.opponent_ability);
    model.opponent_ability = obs.facts.opponent_ability.source === "UNKNOWN" ?
      unknown() : structuredClone(obs.facts.opponent_ability);
  }
  const digest = fingerprint(state(battle));
  const restored = restoreChecked(serialize(battle), digest);
  if (fingerprint(state(restored)) !== digest) throw Error("candidate complete synthetic round-trip failed");
  const view = observed(battle), confirmed = key =>
    ["SYNTHETIC", "GAME_OBSERVED"].includes(obs.facts[key].source);
  const battleApplicable = (!confirmed("weather") || obs.facts.weather.value === view.weather) &&
    (!confirmed("screens") ||
      stableStringify(obs.facts.screens.value) === stableStringify(view.sides[1].conditions)) &&
    (!confirmed("active_opponent") ||
      obs.facts.active_opponent.value === view.sides[1].active[0].species) &&
    (!confirmed("request_state") || obs.facts.request_state.value === view.requestState) &&
    (!confirmed("opponent_item") || obs.facts.opponent_item.value === active.item) &&
    (!confirmed("opponent_mega_state") ||
      obs.facts.opponent_mega_state.value === Boolean(active.species.isMega)) &&
    (!confirmed("opponent_status") || obs.facts.opponent_status.value === active.status) &&
    (!confirmed("opponent_hp_exact") || obs.facts.opponent_hp_exact.value === active.hp) &&
    (!confirmed("opponent_remaining_pp") ||
      obs.facts.opponent_remaining_pp.value === active.moveSlots[0].pp) &&
    (!confirmed("opponent_boosts") ||
      stableStringify(obs.facts.opponent_boosts.value) === stableStringify(active.boosts));
  return {id, model, battle, state_sha256: digest, synthetic_roundtrip: true,
    battle_applicable: battleApplicable};
}
function compatible(c, obs) {
  const f = obs.facts, m = c.model;
  const matches = (key, modelKey = key) => f[key].source === "UNKNOWN" ||
    f[key].source === "INFERRED" || stableStringify(f[key].value) === stableStringify(m[modelKey]?.value);
  if (["own_registered", "opponent_registered", "own_selected", "opponent_selected",
    "active_own", "active_opponent", "opponent_species", "opponent_form",
    "opponent_hp_exact", "opponent_status", "opponent_boosts",
    "opponent_remaining_pp", "opponent_item", "opponent_ability", "opponent_nature",
    "opponent_ap", "opponent_mega_state", "weather", "screens", "request_state"]
    .some(key => !matches(key))) return false;
  return f.opponent_revealed_moves.source === "UNKNOWN" ||
    f.opponent_revealed_moves.value.every(move => m.opponent_moves.value.includes(moveKey(move)));
}
function makeCandidates(obs, ctx, ids = TEMPLATE_IDS, cap = 4) {
  assertObservation(obs, ctx);
  if (!Number.isInteger(cap) || cap < 1 || !Array.isArray(ids) ||
      ids.some(id => !TEMPLATE_IDS.includes(id)) || new Set(ids).size !== ids.length)
    throw Error("invalid candidate scope or cap");
  const evaluated = ids.slice(0, cap).map(id => candidate(ctx, id, obs));
  const rejected = evaluated.filter(c => !compatible(c, obs))
    .map(c => ({id: c.id, reason: "conflicts with confirmed observation"}));
  return {candidates: evaluated.filter(c => compatible(c, obs)), rejected,
    unevaluated_candidate_count: Math.max(0, ids.length - cap)};
}
function setFact(obs, key, value, evidenceId, allowTransition = false) {
  const prior = obs.facts[key];
  if (prior.source !== "UNKNOWN" && prior.source !== "INFERRED" &&
      !allowTransition && stableStringify(prior.value) !== stableStringify(value))
    return {contradiction: key + " has incompatible confirmed values"};
  obs.facts[key] = synthetic(value, evidenceId);
  return {contradiction: null};
}
function applyEvent(obs, candidates, event, ctx, index) {
  if (!EVENT_TYPES.has(event.type)) throw Error("unknown partial-observation event");
  const id = "synthetic-event-" + index;
  obs.evidence.push({id, kind: "SYNTHETIC_FIXTURE"});
  let kept = candidates, contradiction = null;
  const update = (field, value, transition = false) => {
    const result = setFact(obs, field, value, id, transition);
    contradiction ||= result.contradiction;
  };
  switch (event.type) {
    case "move_used":
      update("opponent_revealed_moves", [...new Set([
        ...(obs.facts.opponent_revealed_moves.source === "UNKNOWN" ? [] :
          obs.facts.opponent_revealed_moves.value), event.value])], true);
      kept = kept.filter(c => c.model.opponent_moves.value.includes(moveKey(event.value)));
      break;
    case "item_revealed":
      update("opponent_item", event.value);
      kept = kept.filter(c => c.model.opponent_item.value === event.value);
      break;
    case "item_consumed":
      if (!ctx.dex.items.get(event.value).isBerry)
        throw Error("item_consumed requires a confirmed consumable berry in this fixture");
      update("opponent_consumed_item", event.value);
      update("opponent_item", "", true);
      kept = kept.filter(c => c.model.opponent_item.value === event.value);
      for (const c of kept) c.model.opponent_item = structuredClone(obs.facts.opponent_item);
      break;
    case "ability_revealed":
      update("opponent_ability", event.value);
      kept = kept.filter(c => c.model.opponent_ability.source === "UNKNOWN" ?
        c.model.opponent_form.value === "garchompmega" &&
          currentFormAbilityIsAllowed(ctx, ctx.dex.species.get("garchompmega"),
            ctx.allowed.species.get("garchomp"), ctx.dex.abilities.get(event.value).id) :
        c.model.opponent_ability.value === event.value);
      for (const c of kept) c.model.opponent_ability = structuredClone(obs.facts.opponent_ability);
      break;
    case "mega_seen":
      if (event.value !== true) throw Error("mega_seen requires true");
      update("opponent_mega_state", event.value);
      update("opponent_form", "garchompmega", true);
      update("active_opponent", "garchompmega", true);
      if (["SYNTHETIC", "GAME_OBSERVED"].includes(obs.facts.opponent_ability.source))
        update("opponent_mega_history", {pre_mega_ability: obs.facts.opponent_ability.value});
      obs.facts.opponent_ability = unknown();
      kept = kept.filter(c => c.model.initial_item.value === "garchompite");
      for (const c of kept) {
        c.model.previous_form_ability = structuredClone(c.model.opponent_ability);
        c.model.opponent_ability = unknown();
        c.model.opponent_mega_state = structuredClone(obs.facts.opponent_mega_state);
        c.model.opponent_form = structuredClone(obs.facts.opponent_form);
        c.model.active_opponent = structuredClone(obs.facts.active_opponent);
      }
      break;
    case "choice_lock_revealed":
      update("choice_lock", event.value);
      kept = kept.filter(c => c.model.initial_item.value === "choicescarf" &&
        c.model.opponent_moves.value.includes(moveKey(event.value)));
      for (const c of kept) c.model.choice_lock = structuredClone(obs.facts.choice_lock);
      break;
    case "switch_seen":
      update("active_opponent", event.value, true);
      update("opponent_species", event.value, true);
      update("opponent_form", event.value, true);
      update("choice_lock", "", true);
      for (const field of ["opponent_revealed_moves", "opponent_remaining_pp",
        "opponent_ability", "opponent_item", "opponent_consumed_item",
        "opponent_removed_item", "opponent_mega_state", "opponent_mega_history", "opponent_current_type",
        "opponent_hp_exact", "opponent_status", "opponent_boosts",
        "opponent_ap", "opponent_nature"]) obs.facts[field] = unknown();
      for (const c of kept) {
        c.model.previous_opponent_set = structuredClone(c.model);
        c.model.active_opponent = structuredClone(obs.facts.active_opponent);
        c.model.opponent_species = structuredClone(obs.facts.opponent_species);
        c.model.opponent_form = structuredClone(obs.facts.opponent_form);
        c.model.choice_lock = structuredClone(obs.facts.choice_lock);
        for (const key of ["initial_item", "opponent_moves", "opponent_remaining_pp", "opponent_ability",
          "opponent_item", "opponent_removed_item", "opponent_mega_state", "opponent_hp_exact",
          "opponent_boosts", "opponent_status", "opponent_nature", "opponent_ap"])
          c.model[key] = unknown();
      }
      break;
    case "boost_seen":
      update("opponent_boosts", event.value);
      for (const c of kept) c.model.opponent_boosts = structuredClone(obs.facts.opponent_boosts);
      break;
    case "action_order_seen":
      update("action_history", [event.value]);
      obs.facts.speed_relation = inferred("order_constrains_speed_only",
        "priority, item, ability, boosts and hidden history can change action order");
      break;
  }
  if (!contradiction) assertObservation(obs, ctx);
  return {candidates: kept, contradiction};
}
function simulateCandidates(candidates, current) {
  const sources = candidates.map(c => c.state_sha256);
  const results = candidates.map(c => {
    if (!current) return {id: c.id, action: null, result_sha256: null,
      status: "NOT_RUN_CURRENT_STATE", reason: "observation update or hidden effect invalidates initial synthetic Battle"};
    const branch = copy(c.battle);
    branch.makeChoices("move 1", "move 1");
    return {id: c.id, action: ["move 1", "move 1"], result_sha256: fingerprint(state(branch)),
      status: "SYNTHETIC_HYPOTHESIS_ONLY"};
  });
  return {results, independent: candidates.every((c, i) =>
    fingerprint(state(c.battle)) === sources[i]) &&
    new Set(candidates.map(c => c.state_sha256)).size === candidates.length};
}
function semanticObservation(obs) {
  return {origin: obs.origin, facts: Object.fromEntries(FIELDS.map(k => {
    const fact = obs.facts[k];
    return [k, {source: fact.source,
      ...(Object.hasOwn(fact, "value") ? {value: fact.value} : {}),
      ...(fact.reason ? {reason: fact.reason} : {})}];
  }))};
}
function runCase(entry, ctx) {
  const truth = syntheticTruth(ctx, entry);
  const truthHash = fingerprint(state(truth));
  const observation = projectObservation(truth, ctx, entry);
  let assessment = assertObservation(observation, ctx);
  const generated = makeCandidates(observation, ctx, entry.candidate_ids || TEMPLATE_IDS,
    entry.candidate_cap || 4);
  let candidates = generated.candidates, contradiction = null;
  const rejected = [...generated.rejected];
  for (const [index, event] of (entry.events || []).entries()) {
    const previous = candidates;
    const changed = applyEvent(observation, candidates, event, ctx, index + 1);
    if (changed.contradiction) {
      contradiction = changed.contradiction;
      rejected.push(...previous.map(c => ({id: c.id, reason: "contradictory observation"})));
      candidates = [];
      break;
    }
    candidates = changed.candidates;
    for (const c of previous.filter(x => !candidates.includes(x)))
      rejected.push({id: c.id, reason: "excluded by " + event.type});
  }
  if (!contradiction) assessment = assertObservation(observation, ctx);
  const current = !entry.setup && !(entry.events || []).length &&
    candidates.every(c => c.battle_applicable);
  const switched = entry.events?.some(e => e.type === "switch_seen") || false;
  const simulation = simulateCandidates(candidates, current);
  const snapshotGaps = assessment.missing_internal_state;
  let status = contradiction ? "CONTRADICTORY_OBSERVATION" :
    ["forced", "pending"].includes(entry.setup) ? "UNSUPPORTED" :
    candidates.length === 0 ? "INCONCLUSIVE" :
    ["mega", "wall", "consumed"].includes(entry.setup) ||
    entry.events?.some(e => e.type === "action_order_seen") ? "INCONCLUSIVE" :
    entry.events?.length ? "SYNTHETIC_TEST_PASS" : "CONSISTENT_HYPOTHESES";
  if (!STATUS.includes(status) || !simulation.independent)
    throw Error("partial candidate independence or status failed: " + entry.id);
  return {case_id: entry.id, mechanism: entry.mechanism,
    schema: observation.schema, pinned_showdown_sha: pinned,
    champions_master_sha256: masterHashes(), format: "gen9championsbssregmc",
    evidence_class: "SYNTHETIC_ONLY", truth_setup: entry.setup || "initial_selected_turn",
    truth_state_sha256: truthHash, observation, observation_sha256: fingerprint(observation),
    partial_state_sha256: fingerprint(semanticObservation(observation)),
    observed_fields: assessment.observed_fields, inferred_fields: assessment.inferred_fields,
    unknown_fields: assessment.unknown_fields, evidence_ids: assessment.evidence_ids,
    observation_consistent: !contradiction, contradiction,
    candidate_cap: entry.candidate_cap || 4,
    unevaluated_candidate_count: generated.unevaluated_candidate_count,
    legal_hypothesis_count: switched ? null : candidates.length,
    previous_active_hypothesis_count: switched ? candidates.length : null,
    current_active_hypothesis_count: switched ? null : candidates.length,
    rejected_candidates: rejected,
    hypothesis_set_scope: switched ? "PREVIOUS_ACTIVE_ONLY" : "CURRENT_ACTIVE",
    hypothesis_set_sha256: fingerprint(candidates.map(c => c.id)),
    candidates: candidates.map(c => ({id: c.id, state_sha256: c.state_sha256,
      state_sha256_scope: switched ? "BASE_PREVIOUS_ACTIVE_HYPOTHESIS_ONLY" :
        current ? "CURRENT_SYNTHETIC_HYPOTHESIS" : "BASE_HYPOTHESIS_ONLY",
      synthetic_roundtrip: c.synthetic_roundtrip,
      base_battle_matches_extracted_observation: c.battle_applicable,
      current_state_simulatable: current && c.battle_applicable, model: c.model})),
    candidate_continuations: simulation.results, candidate_independence: simulation.independent,
    candidate_truth_hash_matches: !current ? null :
      candidates.filter(c => c.state_sha256 === truthHash).length,
    complete_snapshot_restorable_from_observation: false,
    unique_truth_restoration: false, missing_internal_state: snapshotGaps,
    legacy_gate_b_gap_fields: GAP_FIELDS,
    real_game_unverified_fields: assessment.real_game_unverified_fields,
    status, reason: contradiction || (candidates.length === 0 ?
      "candidate set exhausted or contradicted; do not fill UNKNOWN" :
      status === "UNSUPPORTED" ? "unknown request or pending queue prevents current-state simulation" :
      status === "INCONCLUSIVE" ? "hidden history, effect duration or action-order conditions remain unknown" :
      "fixed legal hypotheses remain synthetic and do not identify true hidden state")};
}
function run() {
  assertManifest();
  const ctx = context();
  const results = manifest.cases.map(c => runCase(c, ctx));
  const positive = results.filter(r => r.status === "CONSISTENT_HYPOTHESES" ||
    r.status === "SYNTHETIC_TEST_PASS");
  return {schema: "phase0-partial-observation-diagnostics-v1", pinned_showdown_sha: pinned,
    champions_master_sha256: masterHashes(), format: "gen9championsbssregmc",
    gate_A: "UNKNOWN", gate_B: "UNSUPPORTED", adoption: "NO_GO",
    case_count: results.length, mechanism_count: new Set(results.map(r => r.mechanism)).size,
    distinct_complete_truth_state_count: new Set(results.map(r => r.truth_state_sha256)).size,
    distinct_partial_observation_count: new Set(results.map(r => r.partial_state_sha256)).size,
    distinct_hypothesis_set_count: new Set(results.map(r => r.hypothesis_set_sha256)).size,
    distinct_candidate_state_hash_count: new Set(results.flatMap(r => r.candidates.map(c => c.state_sha256))).size,
    candidate_evaluations: results.reduce((n, r) => n + r.candidates.length + r.rejected_candidates.length, 0),
    repetitions: 0, representative_100_cases_met: false,
    counts: Object.fromEntries(STATUS.map(s => [s, results.filter(r => r.status === s).length])),
    synthetic_condition_pass_count: positive.length, results};
}
if (require.main === module) {
  try {process.stdout.write(JSON.stringify(run()) + "\n");}
  catch (e) {process.stdout.write(JSON.stringify({status: "NOT_RUN", reason: e.message}) + "\n"); process.exitCode = 2;}
}
module.exports = {run, runCase, assertManifest, projectObservation, assertObservation,
  makeCandidates, applyEvent, selectedTeams, syntheticTruth, FIELDS, TEMPLATE_IDS};
