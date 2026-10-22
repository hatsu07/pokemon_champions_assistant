"""Verify the included original source copy hashes and original workbook structure (offline)."""
from pathlib import Path
import hashlib,json,zipfile
ROOT=Path(__file__).resolve().parents[1]
meta=json.loads((ROOT/'assets/source_copies_v2_7.json').read_text(encoding='utf-8'))
for e in meta['source_copies']:
 path=ROOT/e['path']
 assert path.is_file(),e['path']
 assert hashlib.sha256(path.read_bytes()).hexdigest()==e['sha256'],e['path']
 if path.suffix=='.xlsx':
  assert zipfile.is_zipfile(path),e['path']
  with zipfile.ZipFile(path) as z:
   assert 'xl/workbook.xml' in z.namelist()
 print('OK:',e['path'])
# Core master reads are not replaced by archive existence/hash checks: see live preloader source.
assert (ROOT/'masters/Pokemon_Champions_MC_team_2026-10-03.csv').is_file()
assert (ROOT/'masters/Pokemon_Champions_MC_live_decision_gate.txt').is_file()
print('OK: source copy integrity; not BlueStacks, gameplay, or LIVE preload validation')
