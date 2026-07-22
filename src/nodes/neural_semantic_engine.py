"""
Neural Semantic Engine — ONNX-quantized CodeBERT for code semantic divergence.

Uses microsoft/codebert-base with INT8 dynamic quantization via ONNX Runtime
for ultra-lightweight, CPU-only semantic inference.

On first run:
- Downloads the CodeBERT model (~420MB) via HuggingFace transformers
- Exports to ONNX format
- Applies INT8 dynamic quantization → ~100MB cached model
- Subsequent runs load directly from cache (zero download overhead)

All inference is fully offline after the initial model preparation.
"""

import os
import logging
import numpy as np

logger = logging.getLogger("DroidAgent")

# Lazy imports to avoid loading heavy libraries at module import time
_DEPENDENCIES_AVAILABLE = None


def _check_dependencies():
    """Check if all required dependencies are available."""
    global _DEPENDENCIES_AVAILABLE
    if _DEPENDENCIES_AVAILABLE is not None:
        return _DEPENDENCIES_AVAILABLE
    try:
        import onnxruntime
        import transformers
        _DEPENDENCIES_AVAILABLE = True
    except ImportError as e:
        logger.warning(
            f"Neural semantic engine dependencies not available ({e}). "
            "NeuralSemanticEngine will return 0.0 for all divergence queries."
        )
        _DEPENDENCIES_AVAILABLE = False
    return _DEPENDENCIES_AVAILABLE


class NeuralSemanticEngine:
    """
    ONNX-quantized CodeBERT engine for computing semantic divergence
    between code fragments.

    Lazy singleton pattern: the model is loaded and cached on first use.
    Subsequent calls reuse the cached ONNX session.

    Memory budget: ~130MB total (100MB quantized model + 30MB overhead).
    Latency: ~15-25ms per inference on modern laptop CPU.
    """

    MODEL_NAME = "microsoft/codebert-base"
    CACHE_DIR = os.path.join("cache", "onnx_model")
    ONNX_MODEL_PATH = os.path.join(CACHE_DIR, "codebert_quantized.onnx")
    MAX_LENGTH = 512

    _instance = None

    @classmethod
    def get_instance(cls):
        """Lazy singleton accessor."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._session = None
        self._tokenizer = None
        self._ready = False

        if not _check_dependencies():
            return

        try:
            self._ensure_model_ready()
            self._ready = True
            logger.info("NeuralSemanticEngine initialized successfully.")
        except Exception as e:
            logger.warning(f"Failed to initialize NeuralSemanticEngine: {e}")
            self._ready = False

    def _ensure_model_ready(self):
        """
        Ensure the quantized ONNX model is ready for inference.
        Downloads, exports, and quantizes on first run; loads from cache thereafter.
        """
        import onnxruntime
        from transformers import AutoTokenizer

        os.makedirs(self.CACHE_DIR, exist_ok=True)

        # Load tokenizer (always needed)
        tokenizer_cache = os.path.join(self.CACHE_DIR, "tokenizer")
        if os.path.exists(tokenizer_cache):
            self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_cache)
        else:
            logger.info(f"Downloading tokenizer for {self.MODEL_NAME}...")
            self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            self._tokenizer.save_pretrained(tokenizer_cache)

        # Check if quantized model already exists
        if os.path.exists(self.ONNX_MODEL_PATH):
            logger.info(f"Loading cached quantized ONNX model from {self.ONNX_MODEL_PATH}")
        else:
            logger.info("First run: preparing ONNX model (this is a one-time operation)...")
            self._export_and_quantize()

        # Create inference session
        sess_options = onnxruntime.SessionOptions()
        sess_options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = 2  # Limit CPU threads for laptop friendliness

        self._session = onnxruntime.InferenceSession(
            self.ONNX_MODEL_PATH,
            sess_options,
            providers=["CPUExecutionProvider"]
        )

    def _export_and_quantize(self):
        """
        Export CodeBERT to ONNX and apply INT8 dynamic quantization.
        This runs only once; the result is cached to disk.
        """
        import torch
        from transformers import AutoModel

        logger.info(f"Downloading {self.MODEL_NAME} model...")
        model = AutoModel.from_pretrained(self.MODEL_NAME)
        model.eval()

        # Create dummy inputs for ONNX export
        dummy_input_ids = torch.ones(1, self.MAX_LENGTH, dtype=torch.long)
        dummy_attention_mask = torch.ones(1, self.MAX_LENGTH, dtype=torch.long)

        raw_onnx_path = os.path.join(self.CACHE_DIR, "codebert_raw.onnx")

        logger.info("Exporting to ONNX format...")
        import io
        import contextlib
        dummy_out = io.StringIO()
        with contextlib.redirect_stdout(dummy_out), contextlib.redirect_stderr(dummy_out):
            torch.onnx.export(
                model,
                (dummy_input_ids, dummy_attention_mask),
                raw_onnx_path,
                input_names=["input_ids", "attention_mask"],
                output_names=["last_hidden_state"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "seq_len"},
                    "attention_mask": {0: "batch", 1: "seq_len"},
                    "last_hidden_state": {0: "batch", 1: "seq_len"}
                },
                opset_version=14,
                do_constant_folding=True
            )

        logger.info("Applying INT8 dynamic quantization...")
        from onnxruntime.quantization import quantize_dynamic, QuantType

        quantize_dynamic(
            raw_onnx_path,
            self.ONNX_MODEL_PATH,
            weight_type=QuantType.QInt8
        )

        # Clean up raw model
        if os.path.exists(raw_onnx_path):
            os.remove(raw_onnx_path)

        model_size_mb = os.path.getsize(self.ONNX_MODEL_PATH) / (1024 * 1024)
        logger.info(
            f"ONNX model quantized and cached at {self.ONNX_MODEL_PATH} "
            f"({model_size_mb:.1f} MB)"
        )

    def _get_embedding(self, code: str) -> np.ndarray:
        """
        Get the mean-pooled embedding vector for a code string.

        FLAW 3 FIX: The original implementation used padding="max_length" (always 512
        tokens) and extracted only the [CLS] token embedding. For short code snippets
        (5-20 lines), this meant 490+ tokens were padding [PAD], causing the CLS
        embedding to reflect mostly padding noise and making all short snippets
        converge to nearly the same vector (observed semantic scores of 0.001-0.035).

        Fix applied:
        1. padding="longest": pad only to the longest sequence in the batch, not to 512.
           For a single snippet, this pads to its actual token length.
        2. Mean-pooling over non-padding tokens: average the hidden states of all REAL
           tokens (where attention_mask=1), not just the single CLS token. This is
           empirically superior for code similarity tasks and far more robust to snippet
           length variation.
        """
        # Tokenize with dynamic padding (only to actual input length)
        inputs = self._tokenizer(
            code,
            max_length=self.MAX_LENGTH,
            padding="longest",       # FLAW 3 FIX: was "max_length"
            truncation=True,
            return_tensors="np"
        )

        # Run inference
        ort_inputs = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64)
        }

        outputs = self._session.run(None, ort_inputs)

        # FLAW 3 FIX: Mean-pool over non-padding tokens (was: CLS token only)
        # last_hidden_state shape: (1, seq_len, hidden_dim)
        last_hidden_state = outputs[0]
        attention_mask = inputs["attention_mask"]  # shape: (1, seq_len)

        # Expand mask to match hidden_dim for broadcasting
        mask_expanded = attention_mask[..., np.newaxis].astype(np.float32)  # (1, seq_len, 1)

        # Sum only the real-token embeddings, then divide by real token count
        sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)  # (1, hidden_dim)
        token_count = np.sum(mask_expanded, axis=1)                          # (1, 1)
        token_count = np.maximum(token_count, 1e-9)                         # prevent divide-by-zero

        mean_embedding = (sum_embeddings / token_count)[0]  # shape: (hidden_dim,)
        return mean_embedding


    def compute_semantic_divergence(self, old_code: str, new_code: str) -> float:
        """
        Compute the semantic divergence between old and new code versions
        using CodeBERT embeddings.

        Uses cosine similarity:
            sim = (v_old · v_new) / (||v_old|| × ||v_new||)
            D_semantic = 1 - sim

        Returns:
            0.0 if semantically identical
            1.0 if completely divergent
            0.0 as fallback if any error occurs (fault-tolerant)
        """
        if not self._ready:
            return 0.0

        try:
            if not old_code or not new_code:
                return 0.0

            old_embedding = self._get_embedding(old_code)
            new_embedding = self._get_embedding(new_code)

            # Cosine similarity
            dot_product = np.dot(old_embedding, new_embedding)
            norm_old = np.linalg.norm(old_embedding)
            norm_new = np.linalg.norm(new_embedding)

            if norm_old == 0 or norm_new == 0:
                return 0.0

            cosine_sim = dot_product / (norm_old * norm_new)

            # Clamp to valid range (floating point precision)
            cosine_sim = max(-1.0, min(1.0, float(cosine_sim)))

            divergence = 1.0 - cosine_sim
            return round(max(0.0, min(1.0, divergence)), 6)

        except Exception as e:
            logger.warning(f"NeuralSemanticEngine error (falling back to 0.0): {e}")
            return 0.0
