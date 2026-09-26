# Formal CLI follow-up: `v35-cli-fix-20260926`

This run used the formal launcher on `hkust4`:

```text
DISPLAY=:99 HOMEMASTER_SKIP_RUNTIME_CHECK=1 ./scripts/homemaster benchmark-alfworld \
  --alfworld-root .runtime/alfworld \
  --alfworld-config .runtime/alfworld/configs/base_config.yaml \
  --trace-root /tmp/hm-v35-cli-fix --split valid_seen --episodes 1 \
  --max-env-steps 20 --max-tool-iterations 60 --max-invalid-actions 30 \
  --memory-mode disabled --run-id v35-cli-fix-20260926
```

Status: **incomplete / not a success gate**.

- Worker source resolution passed through the formal launcher (`HOMEMASTER_REPO_ROOT`).
- Real THOR reset completed and produced PNG frames; worker stderr contained only
  ALFWorld/THOR startup lines and no traceback.
- Provider attempts were recorded and the corrected off-screen navigation policy was
  present in the worker configuration.
- Mimo continued issuing invalid target decisions for longer than the bounded test
  window. The run was terminated and its worker/Unity descendants were explicitly
  killed; no formal `summary.json`, `won=true`, or close receipt was claimed.
- This evidence therefore confirms the launcher and worker path, but cannot close the
  main CLI success requirement. The model behavior remains a separate unresolved gate.
