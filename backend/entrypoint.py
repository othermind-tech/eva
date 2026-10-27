"""
PyInstaller entry point — starts the EVA FastAPI server via uvicorn.
This file is only used in the packaged build, not in development.
"""
import sys
import os

if getattr(sys, 'frozen', False):
    sys.path.insert(0, sys._MEIPASS)
    os.chdir(sys._MEIPASS)
    os.environ['TIKTOKEN_CACHE_DIR'] = os.path.join(sys._MEIPASS, 'tiktoken_cache')

import uvicorn
from main import app  # direct import — avoids uvicorn string-based module lookup

if __name__ == '__main__':
    # B2-1.5 (2026-08-18): was '0.0.0.0' -- EVA is a local single-user
    # desktop assistant, not a multi-host service; binding to all
    # interfaces unnecessarily exposed /auth/login, /auth/signup, and
    # /health to every device on the same LAN/Wi-Fi instead of just this
    # machine. Override via EVA_HOST if a genuine reason to bind wider
    # ever comes up.
    uvicorn.run(
        app,
        host=os.environ.get('EVA_HOST', '127.0.0.1'),
        port=8000,
        log_level='info',
    )
