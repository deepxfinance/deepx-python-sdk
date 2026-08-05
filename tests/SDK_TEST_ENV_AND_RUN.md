# DeepX SDK Tests: Environment Variables and Run Commands

This document summarizes the required environment variables and run commands for SDK test scripts under `deepx-python-sdk/tests`.

## 0. One-Time Setup

Run in the `deepx-python-sdk` directory:

```bash
cd deepx-python-sdk
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip ".[dev]"
export PYTHONPATH=$PWD/src
```

Notes:
- The SDK is pure Python; these tests do not require any native build step.
- For `ApiClient` tests, if `API_BASE_URL` is not set, the default is `https://rest-api-devnet.deepx.fi`.
- Test output controls for script-style tests:
  - `SDK_TEST_VERBOSE=1`: print full response payload
  - `SDK_TEST_FAIL_ONLY=1`: print only failed items

## 1. ApiClient Tests

```bash
pytest -q tests/test_api_client_headers.py tests/test_api_v1_sdk_methods.py
```

Optional:
- `API_BASE_URL`
- `API_KEY`

Remote smoke tests are kept as script-style checks:

```bash
python tests/real_devnet_readonly_smoke.py
python tests/real_api_v1_smoke.py
python tests/real_chain_client_smoke.py
```

## 2. ChainClient Transaction Scripts

These scripts submit real transactions when pointed at a live chain.

```bash
python tests/test_place_order.py
python tests/test_cancel_order.py
python tests/test_close_position.py
python tests/test_spot_place_order.py
python tests/test_spot_cancel_order.py
python tests/test_lending_actions.py
python tests/test_subaccount_actions.py
python tests/test_set_profit_and_loss_point.py
```

Common required:
- `PRIVATE_KEY`
- `ORDER_SUBACCOUNT` or the script-specific subaccount variable

Common optional:
- `SUBSTRATE_WS`
- `EVM_RPC_URL`
- Module precompile overrides such as `PERP_PRECOMPILE`, `SPOT_PRECOMPILE`, `LENDING_PRECOMPILE`, and `SUBACCOUNT_PRECOMPILE`

## 3. ChainClient View Scripts

```bash
python tests/test_perp_views.py
python tests/test_spot_views.py
python tests/test_lending_views.py
python tests/test_subaccount_views.py
python tests/test_system_account.py
```

Common required:
- `EVM_RPC_URL`
- A view target such as `VIEW_SUBACCOUNT`, `ORDER_SUBACCOUNT`, or the script-specific market variable

Optional:
- `SUBSTRATE_WS`
- Module precompile overrides
- `PRIVATE_KEY` for scripts that can use a signer; many view scripts use an all-zero key when unset

## 4. Pytest Unit Tests

```bash
pytest -q
pytest --cov=deepx_sdk --cov-report=term-missing
```

By default, these unit tests do not require chain environment variables.
The coverage command measures line coverage for the SDK package only; remote smoke scripts and real transaction
scripts are intentionally excluded from the default coverage gate. The coverage gate also
excludes the low-level native/RPC transport backends (`_native_py.py`, `_native.py`, `_evm.py`);
those paths are covered by focused unit tests and real smoke scripts because many branches depend
on node, runtime, and third-party client behavior.

## 5. Recommended Manual Regression Order

```bash
# 1) Run pure unit tests first
pytest -q tests/test_decode_abi_compat.py tests/test_native_py_signer.py tests/test_runtime_compat.py tests/test_subaccount_precompile_signatures.py

# 2) Run public API client tests
pytest -q tests/test_api_client_headers.py tests/test_api_v1_sdk_methods.py

# 2.5) Optionally run remote API/WS smoke checks
python tests/real_devnet_readonly_smoke.py
python tests/real_api_v1_smoke.py
python tests/real_chain_client_smoke.py

# 3) Run chain view scripts
python tests/test_perp_views.py
python tests/test_spot_views.py
python tests/test_lending_views.py
python tests/test_subaccount_views.py
python tests/test_system_account.py

# 4) Run real transaction scripts last
python tests/test_place_order.py
python tests/test_cancel_order.py
python tests/test_close_position.py
python tests/test_spot_place_order.py
python tests/test_spot_cancel_order.py
python tests/test_lending_actions.py
python tests/test_subaccount_actions.py
```
