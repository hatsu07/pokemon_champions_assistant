#!/usr/bin/env python3
"""Build v2.3 P0 canonical contracts; no Pokémon mechanics invented."""
import json
from pathlib import Path
P = Path('/mnt/data')
VERSION = '2.3.0'

def prop(t, **kwargs):
    return {'type': t, **kwargs}

def obj(props, **kw):
    return {'type':'object','additionalProperties':False,'properties':props,'required':list(props),**kw}

def arr(item):
    return {'type':'array','items':item}

def nullable(t):
    return {'type':[t,'null']}

def save(name, data):
    path = P/name
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(path.name, path.stat().st_size)

def field_state():
    return obj({'label':prop('string'),'value':{'type':['string','number','boolean','null']},'certainty':{'type':'string','enum':['CONFIRMED','CANDIDATE','UNKNOWN','CONFLICT']},'remaining_turn_candidates':{'type':['array','null'],'items':{'type':'integer','minimum':0}},'evidence_ids':arr(prop('string'))})

def poke():
    return obj({'name':prop('string'), 'moves':arr(prop('string')),'item':nullable('string'), 'ability':nullable('string'), 'nature':nullable('string'), 'evs':nullable('string'), 'role':nullable('string'), 'speed_note':nullable('string'),'preserve_priority':nullable('string'),'mega_form':nullable('string')})

input_props={
 'protocol_version':{'enum':[VERSION]},'request_id':prop('string'),'match_id':prop('string'),
 'phase':{'enum':['SELECT','PREP','NORMAL_COMMAND','PIVOT_TARGET','FAINT_REPLACEMENT','EFFECT_REPLACEMENT','REVIEW']},
 'decision_window_id':prop('string'),'tactical_state_version':{'type':'integer','minimum':0},
 'observation_revision':{'type':'integer','minimum':0},'allowed_action_digest':{'type':'string','pattern':'^sha256:[0-9a-f]{64}$'},
 'deadline_ms_remaining':{'type':'integer','minimum':0},
 'source_manifest':obj({'frozen_at':prop('string'),'files':arr(obj({'name':prop('string'),'sha256':prop('string')}))}),
 'allowed_actions':obj({'completeness':{'enum':['COMPLETE','PARTIAL','UNKNOWN']},'moves':arr(prop('string')),'pivot_moves':arr(prop('string')),'switch_targets':arr(prop('string')),'team_candidates':arr(prop('string')),'mega_available':nullable('boolean'),'choice_lock':nullable('string'),'active_form':nullable('string')}),
 'context':obj({'own_team':arr(poke()),'selected_three':arr(prop('string')),'lead':nullable('string'),'opponent_preview_six':arr(prop('string')),
     'live_matrix_rows_six':arr(prop('string')),'prep':obj({'status':{'enum':['NOT_STARTED','COMPLETE','INCOMPLETE','STALE']},'matchups_18':arr(prop('string')),'preserve':nullable('string'),'stopper':nullable('string'),'closer':nullable('string')}),
     'gate_sha256':prop('string')}),
 'battle_state':obj({'our_active':nullable('string'),'opponent_active':nullable('string'),'our_remaining':arr(prop('string')),'opponent_revealed':arr(prop('string')),
     'opponent_unrevealed_count':nullable('integer'),'hp_status':arr(field_state()),'stat_stages':arr(field_state()),
     'field_effects':arr(field_state()),'statuses':arr(field_state()),'forms_and_types':arr(field_state()),
     'choice_locks':arr(field_state()),'speed_observations':arr(prop('string')),'known_moves_items_abilities':arr(prop('string')),
     'recent_confirmed_events':arr(prop('string')),'unresolved_event_ids':arr(prop('string')),'uncertainties':arr(prop('string'))}),
 'snapshot_quality':obj({'readiness':{'enum':['READY_CONFIRMED','READY_WITH_UNCERTAINTY','BLOCKED']},'critical_unknowns':arr(prop('string')),
     'material_unknowns':arr(prop('string')),'pending_event_ids':arr(prop('string')),'evidence_seq_through':{'type':'integer','minimum':0},
     'reason':prop('string')}),
 'previous_ai_plan':obj({'parent_decision_window_id':nullable('string'),'planned_pivot_target':nullable('string'),'based_on_version':nullable('integer'),'validated_against_current_state':prop('boolean')}),
 'task':prop('string')
}
# The input wire contract is validated by the local server, not fed as strict output schema.
request = {'$schema':'https://json-schema.org/draft/2020-12/schema','title':'Champions API Request Wire Protocol v2.3.0',**obj(input_props)}
save('Pokemon_Champions_API_request_schema_v2_3.json',request)

common={
 'protocol_version':{'type':'string','enum':[VERSION]},'request_id':prop('string'),'match_id':prop('string'),
 'decision_window_id':prop('string'),'tactical_state_version':prop('integer'),'allowed_action_digest':prop('string'),
 'decision_status':{'type':'string','enum':['RECOMMENDED','NO_DECISION']},'reason_code':prop('string'),'warning':nullable('string')
}

out={
 'SELECT': obj({**common,'selected_three':arr(prop('string')),'lead':nullable('string')}),
 'PREP': obj({**common,'prep_status':{'type':'string','enum':['COMPLETE','INCOMPLETE']},
    'matchups_18':arr(obj({'our_pokemon':prop('string'),'opponent_pokemon':prop('string'),'eval_code':prop('string')})),
    'preserve':nullable('string'),'stopper':nullable('string'),'closer':nullable('string'), 'unfinished_reason':nullable('string')}),
 'BATTLE': obj({**common,'action_kind':{'type':'string','enum':['MOVE','SWITCH','NONE']},'move':nullable('string'),'mega':prop('boolean'),'switch_target':nullable('string'), 'planned_pivot_target':nullable('string'),'keep_back':nullable('string')}),
 'REVIEW': obj({**common,'outcome':{'type':'string','enum':['WIN','LOSS','DRAW','UNKNOWN']},'selection_review':prop('string'),'turn_reviews':arr(prop('string')),'support_errors':arr(prop('string')),'player_errors':arr(prop('string')),'structure_issues':arr(prop('string')),'improvements':arr(prop('string'))})
}
for kind, schema in out.items():
    save(f'Pokemon_Champions_API_response_{kind.lower()}_schema_v2_3.json', schema)

# Seed catalog migration: preserve all source values and do NOT infer missing mechanics.
seed=P/'Pokemon_Champions_effect_rules_catalog_seed_v0_1.jsonl'
legacy_schema=json.loads((P/'Pokemon_Champions_effect_rule_schema_v0_1.json').read_text(encoding='utf-8'))
new_schema=dict(legacy_schema)
new_schema['title']='Pokemon Champions Canonical Effect Rule Catalog v0.2.0'
new_schema['required']=['schema_version']+legacy_schema['required']
new_schema['properties']={'schema_version':{'const':'0.2.0'},**legacy_schema['properties']}
save('Pokemon_Champions_effect_rule_schema_v0_2.json', new_schema)

rows=[]
for line in seed.read_text(encoding='utf-8').splitlines():
    x=json.loads(line)
    # Old name allowed only during migration, never emitted in canonical representation.
    if 'observation_required' in x:
        if 'confirmation_required' in x and x['confirmation_required']!=x['observation_required']:
            raise ValueError(f"collision on {x['rule_id']}")
        x['confirmation_required']=x.pop('observation_required')
    x={'schema_version':'0.2.0',**x}
    rows.append(x)
target=P/'Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl'
with target.open('w',encoding='utf-8') as f:
    for x in rows: f.write(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n')
print(target.name, target.stat().st_size, 'entries', len(rows))
