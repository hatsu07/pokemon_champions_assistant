"""Offline package preflight; python scripts/verify_package.py from repository root."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
REQ=[
 'AGENTS.md', 'BUILD_PLAN.md', 'CODEX_START_PROMPT.md',
 'contracts/Pokemon_Champions_API_request_schema_v2_3.json',
 'masters/Pokemon_Champions_MC_live_decision_gate.txt',
 'masters/Pokemon_Champions_MC_live_selection_matrix.txt',
 'masters/Pokemon_Champions_MC_60sec_lookup_master.txt',
 'masters/Pokemon_Champions_MC_team_2026-10-03.csv',
 'baseline_v2_3/champions_contract_runtime_v2_3.py',
 'baseline_v2_3/test_champions_contract_v2_3.py',
]
for f in REQ:
    path=ROOT/f
    assert path.is_file(), f'MISSING: {f}'
    print(f'{f} {hashlib.sha256(path.read_bytes()).hexdigest()[:12]}')
rows=[json.loads(line) for line in (ROOT/'masters/Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
assert len(rows)==892,(len(rows),'expected 892')
assert all(row['schema_version']=='0.2.0' for row in rows)
print(f'OK: files + {len(rows)} effect records. This is NOT an end-to-end game test.')
