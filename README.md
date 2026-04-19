# Memory-Grounded Embodied Task Brain

HomeMaster task brain CLI MVP.

This project demonstrates a memory-grounded embodied task loop with:

- rule-first instruction parsing
- structured memory retrieval
- deterministic high-level planning
- evidence-based verification
- failure-aware recovery
- auditable CLI traces

## Install

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install ".[dev]"
```

Use a non-editable install for Stage 0. In this local uv Python environment,
editable installs generate a hidden `__editable__` `.pth` file that Python
skips, so the package may not import outside pytest.

## Verify

```bash
python -I -c "import task_brain; print(task_brain.__version__)"
pytest -q
ruff check .
```
