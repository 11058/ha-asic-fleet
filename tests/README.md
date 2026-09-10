# Tests

`test_parsers.py` runs the miner-payload parsers against JSON captured from a
live Antminer L7 on Promminer_L7_7007 firmware (`fixtures/`, with the pool
worker name, MAC and IP replaced by placeholders).

They deliberately need neither Home Assistant nor a network:

```
pip install pytest httpx
pytest tests -q
```

`conftest.py` loads the module under a synthetic package so its relative
imports resolve without executing the integration's `__init__.py`, which would
pull in Home Assistant.
