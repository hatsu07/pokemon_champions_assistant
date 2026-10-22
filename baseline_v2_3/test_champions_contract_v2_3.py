import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
import unittest

from champions_contract_runtime_v2_3 import (
    allowed_action_digest, verify_local_schema, validate_wire_input,
    Evidence, EvidenceLedger, assess_snapshot, response_valid_for_window,
    PROTOCOL_VERSION, FOUR_REQUIRED_MASTERS,
)
P=Path(__file__).resolve().parent


def sample_request(phase='NORMAL_COMMAND'):
    names=['ポケモン甲','ポケモン乙','ポケモン丙','ポケモン丁','ポケモン戊','ポケモン己']
    def member(s):
        return {'name':s,'moves':['技A','技B','技C','技D'],'item':None,'ability':None,
                'nature':None,'evs':None,'role':None,'speed_note':None,'preserve_priority':None,'mega_form':None}
    allowed={'completeness':'COMPLETE','moves':['技A','技B','技C','技D'],
             'pivot_moves':[],'switch_targets':['ポケモン乙','ポケモン丙'], 'team_candidates':names,
             'mega_available':False,'choice_lock':None,'active_form':None}
    r={'protocol_version':PROTOCOL_VERSION,'request_id':'req1','match_id':'match1',
       'phase':phase,'decision_window_id':'dw1','tactical_state_version':17,'observation_revision':29,
       'allowed_action_digest':allowed_action_digest(phase,allowed),
       'deadline_ms_remaining':14000,
       'source_manifest':{'frozen_at':'2026-10-21T14:00:00+09:00','files':[{'name':n,'sha256':hashlib.sha256(n.encode()).hexdigest()} for n in sorted(FOUR_REQUIRED_MASTERS)]},
       'allowed_actions':allowed,
       'context':{'own_team':[member(x) for x in (names if phase=='SELECT' else names[:3])],
           'selected_three':[] if phase=='SELECT' else names[:3],
           'lead':None if phase=='SELECT' else names[0],
           'opponent_preview_six':['敵甲','敵乙','敵丙','敵丁','敵戊','敵己'],
           'live_matrix_rows_six':['matrix-'+str(i) for i in range(6)],
           'prep':{'status':'COMPLETE','matchups_18':['pair-'+str(i) for i in range(18)],
                   'preserve':'乙','stopper':'甲','closer':'丙'},
           'gate_sha256':hashlib.sha256(b'gate').hexdigest()},
       'battle_state':{'our_active':'ポケモン甲','opponent_active':'敵甲',
                       'our_remaining':names[:3], 'opponent_revealed':['敵甲'],
                       'opponent_unrevealed_count':2,
                       'hp_status':[], 'stat_stages':[], 'field_effects':[], 'statuses':[],
                       'forms_and_types':[], 'choice_locks':[],
                       'speed_observations':[], 'known_moves_items_abilities':[],
                       'recent_confirmed_events':[],'unresolved_event_ids':[],'uncertainties':[]},
       'snapshot_quality':{'readiness':'READY_CONFIRMED','critical_unknowns':[],
                'material_unknowns':[],'pending_event_ids':[],'evidence_seq_through':100,'reason':'all processed'},
       'previous_ai_plan':{'parent_decision_window_id':None,'planned_pivot_target':None,
                           'based_on_version':None,'validated_against_current_state':False},
       'task':'choose one action'}
    return r


def sample_battle_response(req):
    return {'protocol_version':req['protocol_version'],'request_id':req['request_id'],
        'match_id':req['match_id'],'decision_window_id':req['decision_window_id'],
        'tactical_state_version':req['tactical_state_version'],'allowed_action_digest':req['allowed_action_digest'],
        'decision_status':'RECOMMENDED','reason_code':'ONE_VALID_CHOICE','warning':None,
        'action_kind':'MOVE','move':'技A','mega':False,'switch_target':None,
        'planned_pivot_target':None,'keep_back':None}


class ContractTests(unittest.TestCase):
    def test_catalog_migration_is_lossless(self):
        old=[json.loads(x) for x in (P/'Pokemon_Champions_effect_rules_catalog_seed_v0_1.jsonl').read_text(encoding='utf-8').splitlines()]
        new=[json.loads(x) for x in (P/'Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(old),len(new))
        self.assertEqual(Counter(x['verification_status'] for x in new),Counter(x['verification_status'] for x in old))
        self.assertEqual(Counter(x['source_type'] for x in new),Counter(x['source_type'] for x in old))
        for a,b in zip(old,new):
            verify_local_schema(b,'effect')
            self.assertEqual(b['schema_version'],'0.2.0')
            self.assertNotIn('observation_required',b)
            self.assertEqual({k:v for k,v in b.items() if k!='schema_version'},a)

    def test_wire_validated(self):
        req=sample_request()
        self.assertEqual(validate_wire_input(req),(True,'OK'))

    def test_digest_deterministic_order_independent(self):
        r=sample_request(); a=r['allowed_actions']; b=deepcopy(a)
        b['moves']=list(reversed(b['moves']))
        self.assertEqual(allowed_action_digest(r['phase'],a),allowed_action_digest(r['phase'],b))
        b['choice_lock']='技A'
        self.assertNotEqual(allowed_action_digest(r['phase'],a),allowed_action_digest(r['phase'],b))

    def test_wrong_digest_rejected(self):
        req=sample_request()
        req['allowed_actions']['moves'].append('未確認技')
        self.assertEqual(validate_wire_input(req)[1],'ACTION_DIGEST_MISMATCH')

    def test_missing_team_move_rejected(self):
        req=sample_request(); req['context']['own_team'][0]['moves'].pop()
        self.assertEqual(validate_wire_input(req)[1],'OWN_SELECTED_FOUR_MOVES_MISSING')

    def test_sync_policy(self):
        kwargs={'phase':'NORMAL_COMMAND','window_confirmed':True,'action_completeness':'COMPLETE',
                'own_team_ready':True,'own_active_known':True,'unprocessed_event_ids':['ocr-19'],
                'critical_unknowns':[]}
        self.assertEqual(assess_snapshot(**kwargs,deadline_pressure=False)[0],'WAIT_FOR_SYNC')
        self.assertEqual(assess_snapshot(**kwargs,deadline_pressure=True)[0],'READY_WITH_UNCERTAINTY')
        self.assertEqual(assess_snapshot(**{**kwargs,'action_completeness':'PARTIAL'},deadline_pressure=True)[0],'BLOCKED')

    def test_correction_replays_but_does_not_rewrite_sent_snapshot(self):
        ledger=EvidenceLedger()
        ledger.append(Evidence('a',1,'weather','sun','BATTLE_TEXT','CONFIRMED'))
        frozen=ledger.freeze_snapshot({'weather':ledger.current_state()['weather']['value']})
        v=ledger.tactical_state_version
        ledger.append(Evidence('b',2,'weather','rain','UI_DIRECT','CONFIRMED'))
        self.assertEqual(ledger.current_state()['weather']['value'],'rain')
        self.assertTrue(ledger.current_state()['weather']['disagreement'])
        self.assertGreater(ledger.tactical_state_version,v)
        self.assertEqual(frozen['weather'],'sun')
        ledger.append(Evidence('c',3,'weather','rain','MANUAL_VERIFIED','CONFIRMED',supersedes=('a',)))
        self.assertEqual(ledger.current_state()['weather']['value'],'rain')
        self.assertFalse(ledger.current_state()['weather']['disagreement'])

    def test_same_rank_conflict(self):
        l=EvidenceLedger()
        l.append(Evidence('a',1,'status','poison','BATTLE_TEXT','CONFIRMED'))
        l.append(Evidence('b',2,'status','burn','BATTLE_TEXT','CONFIRMED'))
        self.assertEqual(l.current_state()['status']['certainty'],'CONFLICT')

    def test_candidate_does_not_become_confirmed(self):
        l=EvidenceLedger()
        l.append(Evidence('a',1,'mega_form','megaX','BATTLE_TEXT','CANDIDATE'))
        self.assertEqual(l.current_state()['mega_form']['certainty'],'CANDIDATE')
        self.assertIsNone(l.current_state()['mega_form']['value'])

    def test_pending_event_invalidates_inflight_ai_version(self):
        l=EvidenceLedger(); v=l.tactical_state_version
        self.assertTrue(l.mark_pending_event('event7'))
        self.assertGreater(l.tactical_state_version,v)
        v=l.tactical_state_version
        self.assertFalse(l.mark_pending_event('event7'))
        self.assertEqual(l.tactical_state_version,v)
        self.assertTrue(l.resolve_pending_event('event7'))
        self.assertEqual(l.pending_event_ids,set())
        self.assertGreater(l.tactical_state_version,v)

    def test_dedupe_evidence_and_effect(self):
        l=EvidenceLedger(); a=Evidence('e1',1,'atk_stage',1,'BATTLE_TEXT','CONFIRMED')
        self.assertTrue(l.append(a)); self.assertFalse(l.append(a))
        self.assertTrue(l.commit_effect_once('activation1:effect0',True))
        self.assertFalse(l.commit_effect_once('activation1:effect0',True))
        self.assertFalse(l.commit_effect_once('activation2:effect0',False))

    def test_response_valid_and_stale(self):
        r=sample_request(); out=sample_battle_response(r)
        curr={k:r[k] for k in ('match_id','decision_window_id','tactical_state_version','allowed_action_digest')}
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr),(True,'OK'))
        curr['tactical_state_version']+=1
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr)[1],'STALE_DECISION')

    def test_response_not_in_legal_move_set(self):
        r=sample_request(); out=sample_battle_response(r); out['move']='技Z'
        curr={k:r[k] for k in ('match_id','decision_window_id','tactical_state_version','allowed_action_digest')}
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr)[1],'ILLEGAL_MOVE')

    def test_pivot_requires_plan_and_nonpivot_refuses_fake_target(self):
        r=sample_request(); out=sample_battle_response(r)
        curr={k:r[k] for k in ('match_id','decision_window_id','tactical_state_version','allowed_action_digest')}
        out['planned_pivot_target']='ポケモン乙'
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr)[1],'UNEXPECTED_PIVOT_TARGET')
        r['allowed_actions']['pivot_moves']=['技A']
        r['allowed_action_digest']=allowed_action_digest(r['phase'],r['allowed_actions'])
        out['allowed_action_digest']=r['allowed_action_digest']; curr['allowed_action_digest']=r['allowed_action_digest']
        out['planned_pivot_target']=None
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr)[1],'PIVOT_TARGET_MISSING')
        out['planned_pivot_target']='ポケモン乙'
        self.assertEqual(response_valid_for_window(r,out,actual_current=curr),(True,'OK'))

    def test_select_3_plus_lead(self):
        r=sample_request('SELECT')
        self.assertEqual(validate_wire_input(r),(True,'OK'))
        base={k:r[k] for k in ('protocol_version','request_id','match_id','decision_window_id','tactical_state_version','allowed_action_digest')}
        o={**base,'decision_status':'RECOMMENDED','reason_code':'TEST','warning':None,
           'selected_three':['ポケモン甲','ポケモン乙','ポケモン丙'],'lead':'ポケモン甲'}
        self.assertEqual(response_valid_for_window(r,o,actual_current=base),(True,'OK'))
        o['lead']='ポケモン丁'
        self.assertEqual(response_valid_for_window(r,o,actual_current=base)[1],'ILLEGAL_TEAM_SELECTION')

    def test_prep_cannot_falsely_complete(self):
        r=sample_request('PREP')
        self.assertEqual(validate_wire_input(r),(True,'OK'))
        base={k:r[k] for k in ('protocol_version','request_id','match_id','decision_window_id','tactical_state_version','allowed_action_digest')}
        lead_code='ACT=技A|KO=?|DPS=比較済|PRI=?|S=?|OUR=?|OPP=?|SAFE=?|PROC=役割確認済|LOCK=?|MEGA=?'
        bench_code='S=?|OUR=?|OPP=?|PRI=?|SAFE=?|PROC=役割確認済'
        pairs=[{'our_pokemon':a,'opponent_pokemon':b,'eval_code':lead_code if a==r['context']['lead'] else bench_code}
               for a in r['context']['selected_three'] for b in r['context']['opponent_preview_six']]
        o={**base,'decision_status':'RECOMMENDED','reason_code':'TEST','warning':None,
           'prep_status':'COMPLETE','matchups_18':pairs,'preserve':'甲','stopper':'乙','closer':'丙','unfinished_reason':None}
        self.assertEqual(response_valid_for_window(r,o,actual_current=base),(True,'OK'))
        o['matchups_18']=pairs[:-1]
        self.assertEqual(response_valid_for_window(r,o,actual_current=base)[1],'PREP_MISSING_MATCHUPS_OR_WIN3')

if __name__ == '__main__':
    unittest.main(verbosity=2)
