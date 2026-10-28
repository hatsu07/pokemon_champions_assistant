"""Device-only timing audit and separate observed tactic role regressions."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.real_battle_validation import (
    SCHEMA,MIN_MATCHES,validate_real_battle_packet,import_real_battle_packet
)
from champions_assist.matchup_evidence import validate_review_packet, import_reviews, certified_review_counts

TEAM="c"*32
FINGERPRINT="d"*64
REG="M-C"


class RealBattleGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def packet(self, count=5):
        records=[]
        for i in range(count):
            file=self.root/f"actual-device-capture-{i}.bin"
            file.write_bytes(f"independent-capture-{i}".encode())
            checksum=sha256(file.read_bytes()).hexdigest()
            records.append({
                "match_id":f"device-battle-{i}",
                "source_kind":"REAL_CAPTURE","opponent_kind":"HUMAN_CONFIRMED",
                "outcome":"WIN" if i%2 else "LOSS",
                "observed_opponent_count":6,"prep_matchups_count":18,
                "prep_win_roles_count":3,"screen_recognition_errors":0,
                "stale_advice_errors":0,
                "capture":{"path":str(file),"sha256":checksum},
                "timeline":{
                    "selection_visible_ms":0,
                    "selection_displayed_ms":14000,
                    "selection_confirmed_ms":16000,
                    "prep_finished_ms":87000,
                    "opponent_lead_visible_ms":88000,
                    "turns":[
                        {"action_screen_ms":89000,"recommendation_visible_ms":134000},
                        {"action_screen_ms":136000,"recommendation_visible_ms":161000},
                    ],
                },
            })
        return {"schema":SCHEMA,"team_id":TEAM,
                "team_fingerprint":FINGERPRINT,"regulation":REG,
                "platform":"WINDOWS_BLUESTACKS","human_reviewed":True,
                "matches":records}

    def check(self,p):
        return validate_real_battle_packet(
            p,team_id=TEAM,team_fingerprint=FINGERPRINT,regulation=REG)

    def test_five_distinct_real_files_and_exact_deadlines_are_required(self):
        data=self.packet()
        status=self.check(data)
        self.assertTrue(status["device_evidence_passed"])
        self.assertEqual(status["independent_capture_hashes"],MIN_MATCHES)
        self.assertEqual(status["matches"][0]["selection"]["elapsed_ms"],14000)
        self.assertEqual(status["matches"][0]["prep"]["elapsed_ms"],87000)
        self.assertEqual(status["matches"][0]["turns"][0]["elapsed_ms"],45000)
        self.assertFalse(status["live_ready"])
        self.assertIn("INDEPENDENT_CHAMPIONS_EFFECT_AND_DAMAGE_VALIDATION",
                      status["blocking_requirements"])
        dest=self.root/"audit-input.json"
        dest.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        file,result=import_real_battle_packet(
            data_root=self.root,packet_path=dest,team_id=TEAM,
            team_fingerprint=FINGERPRINT,regulation=REG)
        self.assertTrue(file.is_file())
        self.assertEqual(json.loads(file.read_text())["match_count"],5)
        self.assertEqual(result["match_count"],5)

    def test_current_real_capture_is_not_bluestacks_only(self):
        data=self.packet()
        data["platform"]="WINDOWS_REAL_CAPTURE"
        status=self.check(data)
        self.assertEqual(status["capture_platform"],"WINDOWS_REAL_CAPTURE")
        self.assertTrue(status["device_evidence_passed"])
        for invalid in ("REPLAY","SYNTHETIC","",None,"UNCONFIRMED"):
            data["platform"]=invalid
            with self.subTest(platform=invalid),self.assertRaises(ConfigurationError):
                self.check(data)

    def test_four_matches_never_pass_even_if_all_under_deadline(self):
        self.assertFalse(self.check(self.packet(4))["device_evidence_passed"])

    def test_any_deadline_miss_fails_even_if_four_are_good(self):
        data=self.packet()
        timeline=data["matches"][0]["timeline"]
        # The combined PREP result and selection display miss the 87s budget.
        timeline["selection_displayed_ms"]=87001
        timeline["selection_confirmed_ms"]=87001
        timeline["prep_finished_ms"]=87002
        timeline["opponent_lead_visible_ms"]=88000
        self.assertFalse(self.check(data)["device_evidence_passed"])
        data=self.packet()
        data["matches"][0]["timeline"]["prep_finished_ms"]=87001
        self.assertFalse(self.check(data)["device_evidence_passed"])
        data=self.packet()
        data["matches"][3]["timeline"]["turns"][1]["recommendation_visible_ms"]=224001
        self.assertFalse(self.check(data)["device_evidence_passed"])

    def test_prep_interrupt_and_recognition_error_never_pass(self):
        data=self.packet()
        data["matches"][1]["timeline"]["opponent_lead_visible_ms"]=86999
        self.assertFalse(self.check(data)["device_evidence_passed"])
        data=self.packet()
        data["matches"][0]["screen_recognition_errors"]=1
        self.assertFalse(self.check(data)["device_evidence_passed"])
        data=self.packet()
        data["matches"][0]["stale_advice_errors"]=1
        self.assertFalse(self.check(data)["device_evidence_passed"])

    def test_synthetic_cpu_guess_or_missing_capture_cannot_pass(self):
        for property,value in [
            ("source_kind","SIMULATED_ONLY"),
            ("opponent_kind","CPU_SUSPECTED"),
            ("observed_opponent_count",5),
            ("prep_matchups_count",6),
            ("prep_win_roles_count",1),
            ("outcome","UNKNOWN"),
        ]:
            data=self.packet()
            data["matches"][0][property]=value
            with self.subTest(property=property),self.assertRaises(ConfigurationError):
                self.check(data)
        data=self.packet()
        data["matches"][0]["capture"]["sha256"]="0"*64
        with self.assertRaises(ConfigurationError):
            self.check(data)

    def test_duplicate_match_or_duplicate_capture_rejected(self):
        data=self.packet()
        data["matches"][1]["match_id"]=data["matches"][0]["match_id"]
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data=self.packet()
        data["matches"][1]["capture"]=dict(data["matches"][0]["capture"])
        with self.assertRaises(ConfigurationError):
            self.check(data)

    def test_missing_monotonic_clock_regulation_or_human_review_rejected(self):
        data=self.packet()
        data["matches"][0]["timeline"].pop("opponent_lead_visible_ms")
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data=self.packet()
        data["matches"][0]["timeline"]["selection_confirmed_ms"]=1
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data=self.packet()
        data["regulation"]="M-OLD"
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data=self.packet()
        data["human_reviewed"]=False
        with self.assertRaises(ConfigurationError):
            self.check(data)


class RoleObservationTests(unittest.TestCase):
    def setUp(self):
        self.sig=["a"*64]*6
        self.sources=[
            {"kind":"USER_INGAME_CONFIRMED","observed_at":"2026-10-25T17:00:00+09:00",
             "sha256":"1"*64}
        ]
        self.base={
            "schema":"champions-matchup-review-v1",
            "team_id":TEAM,"regulation":REG,"member_signatures":self.sig,
            "player_reviewed":True,
            "reviews":[{
                "opponent":"ガブリアス","slot":0,"member_signature":self.sig[0],
                "status":"CONDITIONAL","rationale":"実画面で観測したターンだけの情報を記録",
                "reviewer":"player","reviewed_at":"2026-10-25T17:00:00+09:00",
                "sources":self.sources,"conditions":{}
            }]
        }

    def check(self,p):
        return validate_review_packet(
            p,team_id=TEAM,regulation=REG,
            signatures=self.sig,opponents={"ガブリアス"})

    def test_attack_observation_is_not_certified_processor(self):
        case=deepcopy(self.base)
        case["reviews"][0]["role_observations"]={
            "processor":{
                "source_sha256":"1"*64,
                "opponent_hp_before":120,"opponent_hp_after":0,
                "observed_our_move":"じゃれつく",
                "our_action_before_opponent":True,
            }
        }
        got=self.check(case)[0]
        self.assertEqual(got["processing_role"],"UNVERIFIED")
        self.assertEqual(got["safe_switch_role"],"UNVERIFIED")
        self.assertEqual(
            got["role_observations"]["processor"]["evidence"],
            "OBSERVED_ONE_CONDITIONAL_ATTACK")
        self.assertFalse(
            got["role_observations"]["processor"]["direct_processor_certified"])

    def test_hard_switch_not_interchangeable_with_attack(self):
        case=deepcopy(self.base)
        case["reviews"][0]["role_observations"]={
            "safe_switch":{
                "source_sha256":"1"*64,
                "own_hp_before_switch":170,"own_hp_after_entry":97,
                "observed_opponent_move":"じしん","hard_switch_performed":True,
            }
        }
        got=self.check(case)[0]
        self.assertEqual(got["role_observations"]["processor"],"NOT_OBSERVED")
        self.assertFalse(
            got["role_observations"]["safe_switch"]["safe_switch_certified"])

    def test_role_review_roundtrip_preserves_source_observation(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            data=deepcopy(self.base)
            data["reviews"][0]["role_observations"]={"processor":{
                "source_sha256":"1"*64,
                "opponent_hp_before":100,"opponent_hp_after":0,
                "observed_our_move":"じゃれつく",
                "our_action_before_opponent":True,
            }}
            file=root/"input.json"
            file.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            catalog={
                "regulation":REG,
                "member_signatures":self.sig,
                "cells":[{"opponent":"ガブリアス","slot":0}],
            }
            dest=import_reviews(data_root=root,team_id=TEAM,
                                packet_path=file,candidate_catalog=catalog)
            self.assertEqual(
                certified_review_counts(dest,team_id=TEAM,
                                        candidate_catalog=catalog)["CONDITIONAL"],1)
            stored=json.loads(dest.read_text(encoding="utf-8"))
            self.assertTrue(
                stored["reviews"][0]["role_observations"]["processor"]["our_action_before_opponent"])

    def test_non_gameproof_sha_or_false_switch_or_invalid_damage_rejected(self):
        data=deepcopy(self.base)
        data["reviews"][0]["role_observations"]={"safe_switch":{
            "source_sha256":"2"*64,
            "own_hp_before_switch":170,"own_hp_after_entry":97,
            "observed_opponent_move":"じしん","hard_switch_performed":True}}
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data["reviews"][0]["role_observations"]["safe_switch"]["source_sha256"]="1"*64
        data["reviews"][0]["role_observations"]["safe_switch"]["hard_switch_performed"]=False
        with self.assertRaises(ConfigurationError):
            self.check(data)
        data["reviews"][0]["role_observations"]["safe_switch"]["hard_switch_performed"]=True
        data["reviews"][0]["role_observations"]["safe_switch"]["own_hp_after_entry"]=185
        with self.assertRaises(ConfigurationError):
            self.check(data)


if __name__=="__main__":
    unittest.main()
