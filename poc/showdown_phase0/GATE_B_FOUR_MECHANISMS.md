# Gate B: Four linked mechanisms (Phase 0 synthetic PoC only)

## Purpose and gates
Combined observational conditioning of (1) choice-lock, (2) Knock Off removal vs Berry consumption, (3) Mega Garchomp current form/ability with linked earlier Mega history, (4) Rain Dance / Electric Terrain with exact remaining turns and one opponent-side Stealth Rock; existing Reflect/Light Screen conditioning is retained.
Always Gate A=UNKNOWN, Gate B=UNSUPPORTED, adoption=NO_GO. No LIVE integration, trusted winrate, real-game battle restoration or past action/RNG replay is claimed.

## Evidence contract
- Evidence record has id, kind, match_id, turn_epoch, state_revision and, for past-to-current identity, active_instance_id. IDs and metadata are supplied observations, not device attestation. Stable active identity must match across historical removal/Mega evidence and the current request frame.
- Currently observable request/active/HP/boosts and each conditioned current mechanic must be linked to the same request-frame ID. Historical removal/Mega events require their own event evidence, same match_id, at or before the current turn/revision.
- New opponent_removed_item observation is distinct from opponent_consumed_item, and is UNKNOWN by default.
- Incompatible current item/lock/removal, wrong form/ability/stone, future event, missing durations, unmodeled hazards/field effects, ambiguous active side and pending choice are UNSUPPORTED.
- Hypothetical opponent sets are copied before changes. The restored serialized Battle, not a copied model, must match the currently conditioned item/lock/form/ability/field effects. Returned history is explicitly synthetic.

## Limited implementation
| Mechanism | Supported | Rejected / not reconstructed |
|---|---|---|
| Choice lock | Current known Choice Scarf/Band/Specs, move ID, current capture revision | Unknown lock/move/item, old snapshot, history of moves and switching |
| Knock Off | Known removed item, target, prior event and current empty item | Confused Berry consumption, false holder, removal of Mega stones, exact attack event effects |
| Mega | Known current Mega Garchomp, Garchompite, Sand Force, matching observed earlier event | Unknown previous forms/abilities, other Mega forms, full historical action reconstruction |
| Field | Rain Dance and Electric Terrain with exact durations, one opponent-side Stealth Rock; existing screens | Unknown or other field effects, multi-layer hazards, unobserved own-side hazards and effect origins |

## Checks
- Positive: each mechanism and compound Mega+field, Knock Off+field, weather+terrain+hazard+Reflect.
- Negative: stale and mismatched match/turn/revision, wrong holder or form, inconsistent current item, unsupported effect history, unfinished queues.
- Verify immutability, legal fixed sets, isolated serialized round trip, deterministic conditional continuation and per-mechanism fail closed.
- Linux Phase0 and Windows native Node tests plus Windows Desktop CI are required for the final PR HEAD. This cannot itself certify Champions mechanic parity.