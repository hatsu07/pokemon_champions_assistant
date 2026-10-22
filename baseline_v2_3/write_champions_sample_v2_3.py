import json
from pathlib import Path
from test_champions_contract_v2_3 import sample_request, sample_battle_response
from champions_contract_runtime_v2_3 import verify_local_schema,validate_wire_input
p=Path('/mnt/data')
req=sample_request()
# Ground file hashes in real local files, not placeholders.
for item in req['source_manifest']['files']:
    import hashlib
    item['sha256']=hashlib.sha256((p/item['name']).read_bytes()).hexdigest()
req['source_manifest']['frozen_at']='2026-10-21T16:00:00+09:00'  # example time, not the actual preloading event.
valid,reason=validate_wire_input(req)
assert valid,reason
resp=sample_battle_response(req)
verify_local_schema(resp,'battle')
(p/'Pokemon_Champions_API_example_request_v2_3.json').write_text(json.dumps(req,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
(p/'Pokemon_Champions_API_example_response_v2_3.json').write_text(json.dumps(resp,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print('Examples validated')
