"""Create a credential-free source snapshot for opt-in local QA runner self-tests.

Does not supply credentials, authorize spending, remove old receipts or reset any
budget/holdout ledger. Existing live accounting must be carried independently.
"""
import hashlib
import json
import pathlib
import shutil
from datetime import datetime, timezone

root = pathlib.Path(__file__).resolve().parents[1]
qa = root / 'qa_20261025'
names = set(root.glob('*.py')) | set((root / 'tests').glob('*.py')) | set(qa.glob('*.py'))
names.update(root / name for name in ('config.json', 'accounting_floor.json', 'evaluation_seals.json', 'web/index.html', 'README.md', 'HUMAN_REVIEW.md', 'OPERATIONS.md', 'LEARNING.md', 'MEMORY_CONTEXT.md', 'PRODUCT_SPEC.md', 'SCOPE.md', 'ARCHITECTURE.md', 'requirements-runtime.txt', 'requirements-learning.txt', 'requirements-qa.txt', 'tools/freeze_qa.py'))
files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(names)}
manifest = dict(created=datetime.now(timezone.utc).isoformat(), files=files,
                digest=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                status='Source snapshot only; run tests separately. No provider authorization granted.')
for name in files:
    target = qa / 'verified_source' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / name, target)
for name in ('current_build.json', 'verified_build.json'):
    (qa / name).write_text(json.dumps(manifest, indent=2))
print(json.dumps(dict(source_digest=manifest['digest'], files=len(files), paid_calls=0)))
