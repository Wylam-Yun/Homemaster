"""Run-scoped RuntimeSettings and runtime path helpers.

RuntimeSettings is constructed explicitly, never read at import time.

Note: A separate runtime_paths.py was planned (Step 2b) to extract path
resolution from runtime.py, but was deferred because:
1. RuntimeSettings already contains all path fields (runtime_root, debug_root, results_root)
2. Path constants remain in runtime.py where import-time config reads are
   intentionally preserved for backward compatibility (see runtime.py comment)
3. load_runtime_settings() provides explicit path resolution when needed
"""
