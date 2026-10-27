# eva.spec
#
# PyInstaller build spec for the EVA backend.
# Derived from the working Build 2 build-backend.ps1 — do not edit in isolation.
#
# Prerequisites before running:
#   1. Stage onnx_model/ in this directory (done by build-backend.ps1 step 3)
#   2. Stage tiktoken_cache/ in this directory (done by build-backend.ps1 step 3)
#   3. eva.env must exist in this directory (your dev copy is fine — no secrets in it)
#
# Build command (always run via build/scripts/build-backend.ps1, not directly):
#   pyinstaller eva.spec --clean -y
#
# Output: backend-dist/eva-backend/   (~350 MB)
#   eva-backend.exe + _internal/ with all bundled dependencies

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# ── Fully collect packages that have DLLs, data files, or dynamic plugin dirs ─
datas_ort,       bins_ort,       hidden_ort       = collect_all('onnxruntime')
datas_tokenizers, bins_tokenizers, hidden_tokenizers = collect_all('tokenizers')
datas_hf,        bins_hf,        hidden_hf        = collect_all('huggingface_hub')
datas_chroma,    bins_chroma,    hidden_chroma    = collect_all('chromadb')
datas_ddgs,      bins_ddgs,      hidden_ddgs      = collect_all('ddgs')
datas_tiktoken,  bins_tiktoken,  hidden_tiktoken  = collect_all('tiktoken')
datas_tiktokext, bins_tiktokext, hidden_tiktokext = collect_all('tiktoken_ext')
datas_litellm,   bins_litellm,   hidden_litellm   = collect_all('litellm')
datas_mcp,       bins_mcp,       hidden_mcp       = collect_all('mcp')

# ── Source data files bundled into _internal/ ─────────────────────────────────
# Paths are relative to this spec file (backend/ directory)
manual_datas = [
    ('onnx_model',            'onnx_model'),          # all-MiniLM-L6-v2 ONNX model (staged by build script)
    ('tiktoken_cache',        'tiktoken_cache'),       # BPE tokenizer cache (staged by build script)
    ('alembic.ini',           '.'),                    # alembic finds this via os.chdir(_MEIPASS) in entrypoint.py
    ('alembic',               'alembic'),              # DB migration scripts
    ('default_agents',        'default_agents'),       # 12 built-in subagent YAML definitions
    ('default_mcps',          'default_mcps'),         # built-in MCP server definitions (seeded disabled; M-01/M-02)
    ('skills',                'skills'),               # built-in skill SKILL.md files
    ('providers/capability',  'providers/capability'), # per-provider model capability cache JSON
]

# ── Imports made by the tool plugins ──────────────────────────────────────────
# tools/default/*.py are copied into _internal/ AFTER PyInstaller runs and loaded with
# importlib at runtime, so PyInstaller never sees what they import. A backend module
# that only a tool imports (e.g. core.agents.dynamic) would be left out and that tool
# would silently fail to load. Read the tool files here and add what they import.
import ast
import sys
from pathlib import Path

_BACKEND = Path(SPECPATH)


def _is_first_party(dotted):
    base = _BACKEND.joinpath(*dotted.split('.'))
    return base.with_suffix('.py').is_file() or (base / '__init__.py').is_file()


def _tool_hidden_imports():
    found = set()
    for tool in sorted((_BACKEND / 'tools' / 'default').glob('*.py')):
        for node in ast.walk(ast.parse(tool.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                # "from pkg import submodule" imports pkg.submodule; keep it only if it is one
                names += [f'{node.module}.{alias.name}' for alias in node.names
                          if alias.name != '*' and _is_first_party(f'{node.module}.{alias.name}')]
            else:
                continue
            for name in names:
                top = name.split('.')[0]
                if top in sys.stdlib_module_names or top == 'tools':
                    continue
                found.add(name)
    return sorted(found)


a = Analysis(
    ['entrypoint.py'],
    pathex=['.'],
    binaries=(
        bins_ort + bins_tokenizers + bins_hf + bins_chroma
        + bins_ddgs + bins_tiktoken + bins_tiktokext + bins_litellm + bins_mcp
    ),
    datas=(
        datas_ort + datas_tokenizers + datas_hf + datas_chroma
        + datas_ddgs + datas_tiktoken + datas_tiktokext + datas_litellm + datas_mcp
        + manual_datas
    ),
    hiddenimports=(
        hidden_ort + hidden_tokenizers + hidden_hf + hidden_chroma
        + hidden_ddgs + hidden_tiktoken + hidden_tiktokext + hidden_litellm + hidden_mcp
        + [
            # ── Uvicorn (all string-referenced at runtime) ────────────────────
            'uvicorn.logging',
            'uvicorn.loops',
            'uvicorn.loops.auto',
            'uvicorn.protocols',
            'uvicorn.protocols.http',
            'uvicorn.protocols.http.auto',
            'uvicorn.protocols.websockets',
            'uvicorn.protocols.websockets.auto',
            'uvicorn.lifespan',
            'uvicorn.lifespan.on',
            # ── Database ──────────────────────────────────────────────────────
            'aiosqlite',
            'sqlalchemy.dialects.sqlite',
            # ── Auth ──────────────────────────────────────────────────────────
            'passlib.handlers.bcrypt',
            'jose',
            # ── Keyring (Windows Credential Manager) ─────────────────────────
            'keyring',
            'keyring.backends',
            'keyring.backends.Windows',
            # ── Document parsers ──────────────────────────────────────────────
            'pypdf',
            'docx2txt',
            # ── Misc ──────────────────────────────────────────────────────────
            'multipart',
            'yaml',
            'tiktoken_ext',
            'tiktoken_ext.openai_public',
            # ── Dynamically imported modules (importlib.import_module) ────────
            # PyInstaller static analysis cannot follow these — must be explicit
            'prompts',
            'prompts.coordinator',
            'prompts.memory',
            'prompts.planner',
            'prompts.project',
            'prompts.soul',
            'prompts.verifier',
            'core.shell',
            'core.shell.executor',
            'core.shell.safety',
            'core.shell.session',
        ]
        + _tool_hidden_imports()
    ),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Explicitly excluded — torch was replaced by onnxruntime in Build 2
        'torch', 'torchvision', 'torchaudio',
        'sentence_transformers',
        'transformers',
        # optimum imports transformers at module level — causes PyInstaller to hang for hours
        'optimum',
        'tensorflow', 'keras',
        'matplotlib', 'PIL', 'cv2',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='eva-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=True: stdout/stderr are captured by Electron main.js via proc.stdout/proc.stderr
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['*.dll'],   # UPX-compressing DLLs causes crashes on some Windows versions
    name='eva-backend',
)
