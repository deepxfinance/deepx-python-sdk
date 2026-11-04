# Dependency security

Updated November 4, 2026. These changes are on the SDK branches and do not alter
the package version or previously published tags.

## HTTP dependencies

The SDK requires `urllib3>=2.8.0,<3.0.0`; the lockfile selects `2.8.0`. This
updates the transitive HTTP dependency used by `requests` / `substrate-interface`
and addresses the advisories fixed in urllib3 2.8.0:

- `GHSA-vxq7-64xx-v4gw`: unbounded chunk-size line buffering.
- `GHSA-gh4c-6fx4-qh6g`: an infinite loop decoding chunked Deflate responses.
- `GHSA-8988-9cw3-xx77`: HTTPS proxy TLS configuration isolation.

Source deployments must synchronize their environment with `uv sync --locked`.
Existing installations do not acquire the changes by updating Git alone.

## Native private-key operations

`coincurve` is a required dependency: `>=21.0.0,<22.0.0` on Python 3.12/3.13,
and `>=20.0.0,<21.0.0` on Python 3.14+. SDK-created private keys
explicitly use the eth-keys `CoinCurveECCBackend` backed by libsecp256k1. This
covers EVM transactions, Substrate extrinsics (sync, async, and standalone
signing), quota-claim messages, and address derivation from private keys.

The SDK does not change process-wide eth-keys settings. Selecting the pure-Python
backend through `ECC_BACKEND_CLASS` does not override SDK-owned private keys.
If the native backend cannot load, private-key operations fail instead of falling
back. Read-only API calls do not initialize the signing backend.

Install from the updated branch with its dependencies; do not use `--no-deps`.
An environment without a compatible coincurve wheel needs a C build toolchain,
CMake, and access to the pinned libsecp256k1 source fetched by coincurve's build.
Validate installation on the actual deployment platform before rollout.

Coincurve 21 has no CPython 3.14 wheels, and its source build fails with the
current CFFI license-file layout. On Python 3.14, the SDK therefore uses
coincurve 20 with a compatible scikit-build-core build constraint. `uv sync
--locked` applies the checked-in uv configuration. For pip source installation,
use pip 25.3+ with its build-constraint option from the repository root:

```bash
python -m pip install --build-constraint constraints/native-build.txt .
```

This is still native libsecp256k1 signing; there is no pure-Python fallback.

## Remaining ecdsa dependency finding

`substrate-interface` still requires `ecdsa`, so the package remains installed
and dependency scanners may report `CVE-2024-23342` / `GHSA-wj6h-64fc-37mp`.
There is no upstream patched version. Its advisory describes Minerva timing
attacks on P-256 and `ecdsa.SigningKey.sign_digest()`; the upstream security
policy excludes side-channel fixes.

The SDK accepts raw secp256k1 private keys. Its private-key operations use the
native backend above, not python-ecdsa signing or mnemonic/BIP32 derivation.
Regression tests block both the pure-Python eth-keys private-key backend and
python-ecdsa signing while exercising the SDK's signing paths. They also check
pre-change signature vectors and failure when coincurve is unavailable.

This is a mitigation and reachability assessment, not removal of the vulnerable
dependency or a guarantee against every side channel. Any scanner exception needs
the project's security review and periodic reassessment. Code in an application
that uses python-ecdsa directly is outside this SDK mitigation. Removing the
dependency entirely requires a compatible upstream change or replacement of the
relevant upstream dependency.

## Upstream references

- [urllib3 security advisories](https://github.com/urllib3/urllib3/security/advisories)
- [python-ecdsa advisory](https://github.com/tlsfuzzer/python-ecdsa/security/advisories/GHSA-wj6h-64fc-37mp)
- [python-ecdsa security policy](https://github.com/tlsfuzzer/python-ecdsa/blob/master/SECURITY.md)
- [eth-keys backends](https://github.com/ethereum/eth-keys#pluggable-ecc-backends)
- [coincurve](https://github.com/ofek/coincurve)
- [pip build constraints](https://pip.pypa.io/en/stable/user_guide/#build-constraints)
