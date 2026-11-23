# Contributing to `qentropyd`

Contributions are welcome! Please ensure all code adheres to strict cryptographic engineering standards:

1. **No Stubs or Mocked Cryptography:** All statistical tests, health checks, and kernel interfaces must be mathematically exact and production-ready.
2. **Type Safety & Linting:** Run `ruff check src tests` and `mypy src tests` before submitting a PR.
3. **Test Coverage:** Add `pytest` cases for any new entropy source, validator, or sink behavior, including adversarial/degenerate input vectors.
4. **English Only:** All code, docstrings, comments, and documentation must be written in English.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```
