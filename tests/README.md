# Contributing tests

Install the SDK and test dependencies, then run the unit suite:

```bash
python -m pip install '.[dev]'
python -m pytest -q
```

Unit tests use mocked network calls and do not require a wallet or live chain.
Remote checks and scripts are opt-in; see
[environment variables and run commands](SDK_TEST_ENV_AND_RUN.md).

## Public test keys

Offline signing tests share `tests/_public_test_keys.py`. It derives its key
from the integer 1, a deliberately public test value, and uses the address
`0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf`. Never treat this key as a secret.

- Use the shared fixture only for offline tests with mocked network calls or
  an isolated local development chain.
- Never fund public test-key addresses on shared networks or with assets of
  real value. Anyone who knows the key can control those accounts.
- Do not use personal keys or load local secrets in unit tests.
- A committed key remains public even after it is removed from the current
  tree. If a funded wallet ever used one, move its assets and permissions to a
  fresh key.

## Remote test wallets

Tests that sign and submit remote transactions must use a dedicated test wallet.
Supply its key through `PRIVATE_KEY` or ignored local configuration; CI should
use secret variables. Keep wallet files and credentials out of Git and logs.

Missing credentials must explicitly fail or skip before any transaction can be
submitted. Never fall back to a public test key. Existing transaction scripts
require `PRIVATE_KEY`; view-only scripts may use a zero-key placeholder because
they do not sign or submit transactions.
