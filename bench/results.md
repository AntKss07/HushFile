# HushFile — Model Benchmark Results

Published benchmark comparing local open-weight models on a labeled test set across categories (Finance, Work, Education, Health, Travel, Receipts, Code, Screenshots, Archives).

| Model | Device | Category Accuracy | Mean Time / File | Share to _Review | Peak VRAM |
| --- | --- | --- | --- | --- | --- |
| `qwen3:4b` | CUDA | **100.0%** | 45.28s | 15.0% | 3741 MB |
| `rule-fallback` | CUDA | **60.0%** | 3.72s | 80.0% | 3756 MB |

### Key Takeaways:
1. **Privacy & Offline:** Runs 100% locally with zero cloud API dependencies.
2. **Graceful Safety:** Low-confidence documents route securely to `_Review/` rather than misfiling.
3. **CPU Fallback:** CPU-only mode provides dependable organization without requiring a dedicated GPU.