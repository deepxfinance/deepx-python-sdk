# MVCC SDK testing and rollout

Status: source candidate `0.2.7rc3`, October 14, 2026. Public testnet has moved
to MVCC; funded-trading acceptance is still pending.

## Branch and version policy

- Development began on `feat/mvcc-compat`, based on stable SDK commit `19f4c0f`.
- `devnet` is the internal-validation branch; changes merge into `main` for the
  public testnet. Current source targets `0.2.7rc3`. This does not imply funded
  trading or sustained-availability acceptance has passed.
- Published `v0.2.6`, `v0.2.7rc1`, and `v0.2.7rc2` remain immutable. The `rc3`
  source has not been published as a release. Pin exact published tags for bots.
- Once chain deployment and the acceptance checks below are confirmed, set version
  `0.2.7` and publish `v0.2.7`. Recheck the available version before tagging if
  another release has occurred meanwhile.

Changing chain internals does not require advertising a new SDK `net` option.
Compatibility is selected from metadata, not runtime-version thresholds or hostnames.

## Explicit staging configuration

The following deployment identities were verified via read-only RPC on
September 30, 2026. This is a historical snapshot; see the October 14 adaptation
below for the current public-testnet identity. These deployments must not be
used as automatic failover endpoints for one another.

| Deployment | HTTP RPC | Runtime | EVM chain ID | Event storage |
| --- | --- | --- | --- | --- |
| MVCC development | `https://rpc-devnet-mvcc.deepx.fi` | 215 | 4855 | `System.Events` |
| MVCC testnet | `https://rpc1-testnet1.deepx.fi` | 372 | 4856 | `System.Events` |
| Existing public testnet | `https://rpc-testnet.deepx.fi` | 371 | 4846 | `System.EventsMap` |

Always recheck `eth_chainId` and `chain_getBlockHash(0)` before a cutover. The three
deployments had different genesis hashes in the September 30 snapshot. Do not combine them in one
endpoint pool, reuse signed transactions across them, or retain pending trackers
while changing the client's chain.

For chain clients, explicitly set `substrate_ws` to the deployment's **verified WS
endpoint**, and set `evm_rpc_url` / explicit `chain_id` for EVM calls when relevant.
Recovery endpoints must belong to the same chain. Both
`wss://rpc-devnet-mvcc.deepx.fi` and `wss://rpc1-testnet1.deepx.fi` passed read-only
RPC and consecutive finalized-head subscription checks. The first development
connection had a transient TLS failure; retries succeeded. This verifies basic
WS connectivity, not transaction subscriptions or sustained recovery under load.

For example, explicitly configure the selected deployment's WS URL:

```python
import os
from deepx_sdk import AsyncChainClient

client = AsyncChainClient(
    substrate_ws=os.environ["MVCC_SUBSTRATE_WS"],
    private_key=os.environ["PRIVATE_KEY"],
    subaccount=os.environ["SUBACCOUNT"],
)
```

During the September 30 staging rollout, no matching MVCC REST or business WS
endpoint had been confirmed. The October 14 public-testnet REST checks are
recorded below; transaction integration and business WS acceptance still require
verification. For isolated staging chains, use explicit market IDs/pairs from
the selected chain rather than resolving them through the public-testnet API.

## Compatibility changes

- All SDK-generated signed Substrate calls support `CheckNonceEra` with the
  default immortal era. The new runtime intentionally keeps its `0x00` encoding
  and genesis-hash signing data compatible with the legacy immortal era. No global
  metadata or dependency monkeypatch is used. Legacy mortality extensions remain
  supported. Mortal `NonceEra::Mortal(valid_until)` is **not** exposed by this SDK;
  it must not be confused with legacy period/phase mortality.
- Event layout is selected from metadata. MVCC skips `Threads` and `EventsMap`
  probes. Recovery still checks block extrinsic hashes before fetching any events.
- For matched blocks, the event decoder checks the runtime at the **parent**,
  uses the execution metadata, and caches historical snapshots by runtime version
  (bounded to four). This handles an upgrade block whose post-state advertises a
  different runtime. Missing metadata/state remains unresolved, not a successful
  execution or evidence of non-inclusion.
- Sync receipts and inclusion scans locate extrinsics by hashing the raw SCALE
  bytes returned by `chain_getBlock` when upstream receipt index lookup fails.
  Upstream receipt lookup decodes the whole block and does not recognize
  `CheckNonceEra`, so it can fail on an unrelated signed transaction before
  reading otherwise valid events. A status decode exception now falls back to
  extrinsic-scoped block events. For calls without an expected business event,
  inclusion alone is not success: `System.ExtrinsicSuccess` is required.
  Explicit scoped failures are checked before accepting an expected event.
- Pallet error names are resolved from that metadata, not just a static enum
  index table. Example: `24_13` is `NoBorrow` on runtime 371, but
  `SpotADLNotReady` on the MVCC test deployments.
- Chain order views are current state, not history. MVCC prunes terminal orders;
  an order-not-found response must not trigger blind resubmission. Reconcile with
  events or the matching indexer.

This work does not add a liquidation/ADL client or change quota-claim REST routes.

## Validation

Offline regression tests (no chain submissions):

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Explicit read-only checks against all three deployments:

```bash
PYTHONPATH=src .venv/bin/python tests/real_mvcc_readonly_smoke.py \
  --rpc https://rpc-devnet-mvcc.deepx.fi \
  --rpc https://rpc1-testnet1.deepx.fi \
  --rpc https://rpc-testnet.deepx.fi
```

The script generates a throwaway key, reads metadata, locally signs four order /
cancel calls, independently reconstructs their signature payloads using the
original metadata types, verifies signatures and hashes, and decodes a finalized
block's events. It neither loads a funded key nor broadcasts transactions. Dummy
market/pair/subaccount values test encoding only, not business validation.
It also replays up to three successful finalized sync receipts per chain, checking
raw-hash index lookup, execution status, and the inclusion-scan receipt path.
`sync_receipts` records upstream decode errors separately from the SDK result.

For a short **read-only** reconnect check on each new chain (reports are local):

```bash
PYTHONPATH=src .venv/bin/python tests/real_mvcc_recovery_smoke.py \
  --ws wss://rpc-devnet-mvcc.deepx.fi \
  --genesis 0xce6a2adf967b506ec36b776ae27a11cd8cb2f358216e992b5be2904b20769880 \
  --seconds 60 --reconnects 2 --report scratch/devnet-recovery.json
PYTHONPATH=src .venv/bin/python tests/real_mvcc_recovery_smoke.py \
  --ws wss://rpc1-testnet1.deepx.fi \
  --genesis 0x87b3c18538a2c969775ffbd81beb3d6d8c9cdb22ffd259915230e61124b0fdd7 \
  --seconds 60 --reconnects 2 --report scratch/testnet1-recovery.json
```

This checks chain identity, event decoding, replays an **already-finalized**
extrinsic locally, forces RPC reconnects, and observes scan health. It submits
no transaction and is not proof of recovery for an actual pending order or of
24-hour availability. Optional `--historical-block HEIGHT` probes archive
retention; unavailable history is marked `UNVERIFIED` rather than success.
The HTTPS endpoints serve EVM calls, while Substrate methods must use `wss://`.

Before publishing a candidate, use designated funded **test** accounts to verify:

1. Perp/spot place and cancel through sync, async, and standalone-signing paths.
   Confirm expected business events and finalization, not merely RPC acceptance.
2. Matched-network REST submit, order lookup, and business WS if the backend is
   included in the rollout; unchanged method names alone do not prove compatibility.
3. At least 24 hours of bot operation on each MVCC deployment. Track inclusion /
   finalization latency, unresolved transactions, capacity, reconnects, scan ranges,
   RPC deadlines, and unhandled task exceptions.
4. Deliberately disconnect transaction WS while pending; verify recovery from an
   independent same-chain endpoint, including transactions already in a block.
5. Recover across a runtime upgrade; test pruned/unavailable historical state and
   transport timeouts. Confirm these do not become false success or `NOT_INCLUDED`.
6. Confirm terminal-order lookups do not cause duplicate orders, and legacy
   testnet remains compatible with the same SDK build.

`BlockHashCount = 256` is not an RPC retention guarantee. Operators must document
block-body and historical-state retention, and provide archive recovery access
when required by the bot's maximum outage duration.

## Production cutover and rollback

1. Freeze the accepted SDK commit, chain build, runtime metadata, backend build,
   endpoint mapping, genesis hash, and chain ID as one deployment manifest.
2. Finish/reconcile outstanding transactions before switching networks. Bring up
   a new SDK instance; never blindly replay unknown outcomes on another chain.
3. Deploy the chain/backend and validate their public endpoints. Update SDK
   defaults only if the public endpoint mapping or chain ID actually changes.
4. Canary the tested SDK with limited traffic, then expand after recovery and
   business-result metrics remain healthy. Publish the immutable stable release.
5. Roll back bot/SDK configuration as a matched set. `0.2.6` is not a compatible
   rollback client for an MVCC runtime; retain a known-good MVCC candidate. A chain
   state migration cannot be undone by downgrading the Python package. Preserve
   transaction hashes/nonces and reconcile unknown outcomes before resuming.


## October 8, 2026 receipt follow-up

The local `deepdex-node` checkout is on `pyth-api-update`; the MVCC comparison
used the local `origin/testnet-mvcc-backend` / `origin/devnet-mvcc-backend` refs,
not that legacy runtime. Those refs use `CheckNonceEra` and pin
`polkadot-sdk-par`'s `mvcc-backend` at `95cf78190b63f7e75226964bb3d42d526573a353`.
Neither the node nor backend was changed.

- Offline regression: **651 passed, 1 skipped**. The temporary-file cleanup
  warning was also present before this fix (636 passed, 1 skipped).
- Read-only compatibility and finalized sync receipt replay passed on MVCC
  development runtime **217**, MVCC testnet runtime **374**, and public testnet
  runtime **371**. Each chain locally signed and verified four calls. Receipt
  replay covered one / three / three successful extrinsics respectively.
- On MVCC testnet, upstream receipts failed with `Index '22' not present in Enum
  type mapping`; the SDK fallback confirmed those same receipts from scoped
  events. The development sample contained only the timestamp inherent, so it
  does not independently exercise a signed MVCC receipt.
- Reports are local under `scratch/mvcc-acceptance/`:
  `read-only-receipt-fix.jsonl`, `regression-receipt-fix.log`, and
  `testnet1-onboarding-readonly-recheck.json`. This run submitted **zero**
  transactions and loaded no funded private key.
- The earlier onboarding wallet now has a subaccount named
  `sdk-mvcc-acceptance-20261008`. Do not repeat initialization blindly. Its old
  report lacks an extrinsic hash and block hash, so that specific transaction
  remains unverified; account state alone does not identify its receipt.

This fixes the synchronous receipt blocker, not the remaining funded order /
cancel acceptance, same-chain REST / business WS verification, pending-order
recovery, or 24-hour availability validation described above.


## rc2 release validation

- Release recheck on October 8, 2026: **651 passed, 1 skipped** on both
  Python **3.12.13** and **3.14.4**, with the same pre-existing temporary-file
  cleanup warning. The Python 3.12 environment installed the built wheel and
  its dependencies; an isolated import confirmed package version `0.2.7rc2`.
- Fresh read-only checks again passed on runtimes **217 / 374 / 371**, with four
  locally signed and verified calls per chain and **1 / 3 / 1** successful sync
  receipt replays respectively. No transaction was submitted.
- Wheel and source distribution passed `twine check`. Archive inspection
  confirmed the receipt fix, regression tests in the source distribution, and
  exclusion of local `scratch/`, `.env`, and Git data. Release assets include
  both packages and `SHA256SUMS.txt`.
- Recheck reports remain local under `scratch/release-0.2.7rc2/`.
  This validation does not remove the funded-order, integration, recovery, or
  sustained-availability limitations above; `v0.2.7rc2` remains a prerelease.


## October 14, 2026 public-testnet adaptation

The public RPC aliases now return chain ID **4856**, runtime **376**, and genesis
`0x87b3c18538a2c969775ffbd81beb3d6d8c9cdb22ffd259915230e61124b0fdd7`.
The September 30 table above is historical; its old public-testnet identity no
longer describes these aliases. Recreate clients and reconcile old-chain pending
transactions separately. The old default bridge address has no code on the new
chain; the SDK requires an explicit verified bridge contract.

REST source methods now expose protocol statistics, asset and perpetual prices,
bad debts, bankruptcy records, and REST transaction-status lookup. Removed query
parameters fail before HTTP submission. Supported cursors are strings copied
from `nextCursor`; clearing a removed filter silently is not permitted. The
liquidation-type filter uses `liqType`. Current backend order history rejects
`status`, so the SDK does not add that filter yet.

Internal deployments continue to use explicit endpoint overrides on `devnet`.
The public network registry does not add an internal network option.


Validation for this source candidate:

- Python 3.14 regression: **690 passed, 1 skipped**, with the pre-existing
  temporary-file cleanup warning. Overall coverage is **95.26%**, compared with
  **95.25%** on the unchanged baseline; the repository's **99%** coverage gate
  still fails. API module coverage is **99.47%**. The threshold is unchanged.
- Python 3.12 installed-wheel checks: **237 passed**, with the same warning.
- Read-only queries passed on both devnet and testnet for statistics, asset and
  perpetual prices, two cursor-paginated trade pages, and asynchronous statistics.
  Public-testnet bad-debt and bankruptcy-record queries also passed. No
  transaction was submitted.
- Wheel and source distribution passed `twine check`; archive inspection
  confirmed the expected source, version, and regression tests and excluded
  local scratch data. Reports remain under `scratch/sdk-testnet-adaptation/`.
