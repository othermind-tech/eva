# core/rag/embed.py
#
# Direct ONNX inference — no torch, no sentence_transformers.
# All imports (including onnxruntime) are lazy: deferred to first embed_text()
# call so the frozen process is fully set up before native DLLs are loaded.
#
# Model resolution order:
#   1. Bundled onnx_model/ dir (PyInstaller build — fully offline)
#   2. HuggingFace Hub download (dev environment)
#
from __future__ import annotations
import logging
import os
import sys

logger = logging.getLogger(__name__)

_MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
_session = None
_tokenizer = None


def _resolve_model_paths() -> tuple[str, str]:
    """Return (model_path, tokenizer_path) — bundled first, HF Hub fallback."""
    # Frozen (PyInstaller) build: model is bundled in _internal/onnx_model/
    if getattr(sys, 'frozen', False):
        bundle_dir  = sys._MEIPASS
        model_path  = os.path.join(bundle_dir, 'onnx_model', 'model.onnx')
        tok_path    = os.path.join(bundle_dir, 'onnx_model', 'tokenizer.json')
        if os.path.exists(model_path):
            logger.info("[EMBED] Using bundled ONNX model from %s", bundle_dir)
            return model_path, tok_path

    # Dev environment: download from HuggingFace Hub (cached after first use)
    from huggingface_hub import hf_hub_download
    logger.info("[EMBED] Downloading ONNX embedding model from %s", _MODEL_REPO)
    model_path = hf_hub_download(_MODEL_REPO, "onnx/model.onnx")
    tok_path   = hf_hub_download(_MODEL_REPO, "tokenizer.json")
    return model_path, tok_path


def _load():
    global _session, _tokenizer
    if _session is not None:
        return _session, _tokenizer

    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    model_path, tok_path = _resolve_model_paths()

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 4
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    _session = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )

    _tokenizer = Tokenizer.from_file(tok_path)
    _tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=128)
    _tokenizer.enable_truncation(max_length=128)

    logger.info("[EMBED] ONNX embedding model ready")
    return _session, _tokenizer


def _mean_pool(hidden, mask):
    import numpy as np
    mask_f = mask[:, :, np.newaxis].astype(np.float32)
    summed = (hidden * mask_f).sum(axis=1)
    counts = mask_f.sum(axis=1).clip(min=1e-9)
    pooled = summed / counts
    norm = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
    return pooled / norm


def embed_text(text: str) -> list[float]:
    import numpy as np
    logger.debug("[EMBED] Embedding text", extra={"text_length": len(text)})
    session, tokenizer = _load()

    enc = tokenizer.encode(text)
    input_ids      = np.array([enc.ids],            dtype=np.int64)
    attention_mask = np.array([enc.attention_mask], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    outputs = session.run(None, {
        "input_ids":      input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": token_type_ids,
    })

    embedding = _mean_pool(outputs[0], attention_mask)
    return embedding.squeeze().tolist()
