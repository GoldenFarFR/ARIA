# Smoke test mémoire vectorielle Chroma (local uniquement)
# Usage: .\test-vector-memory.ps1 [-EnableVector]

param([switch]$EnableVector)

$ErrorActionPreference = "Stop"
$core = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $core "src"
if ($EnableVector) { $env:ARIA_VECTOR_MEMORY = "true" }

python -c @"
import asyncio, json, tempfile
from pathlib import Path
from aria_core import paths
from aria_core.memory.vector.chroma_client import reset_client_cache
from aria_core.memory.llm_context import build_llm_context
from aria_core.memory.vector.chroma_store import store, search, vector_store_status
from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

tmp = Path(tempfile.mkdtemp(prefix='aria-vector-test-'))
vector_on = __import__('os').environ.get('ARIA_VECTOR_MEMORY', '').lower() in ('1', 'true', 'yes')
configure_test_runtime(data_dir=tmp, settings=AriaRuntimeSettings(aria_vector_memory=vector_on))
paths.configure_data_dir(tmp)
reset_client_cache()

async def main():
    if vector_on:
        await store('insight', 'Smoke vector ARIA test', metadata={'source': 'script', 'topic': 'smoke'})
    st = vector_store_status()
    hits = await search('smoke vector', entry_type='insight', limit=2) if vector_on else []
    ctx = ''
    if vector_on:
        ctx = await build_llm_context(public=False, query_hint='smoke vector ARIA memory test')
    print(json.dumps({
        'status': st,
        'search': hits,
        'llm_has_vector_recall': 'Rappel sémantique' in ctx,
    }, indent=2, ensure_ascii=False))

asyncio.run(main())
"@

Write-Host "test-vector-memory OK (vector=$EnableVector)" -ForegroundColor Green