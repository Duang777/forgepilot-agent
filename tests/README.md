# Test Layout

- `unit/`: isolated logic tests without external process dependencies.
- `integration/`: API/service/storage interactions and route behavior.
- `contract/`: request/response/SSE compatibility assertions.
- `e2e/`: end-to-end workflow checks including planning/execution chains.

Run all tests:

```bash
python -m pytest -q
```

Run a specific layer:

```bash
python -m pytest -q tests/unit
python -m pytest -q tests/integration
python -m pytest -q tests/contract
python -m pytest -q tests/e2e
```
