# SDK credential and resource boundary review

This records the disposition of [issue #6](https://github.com/deepxfinance/deepx-python-sdk/issues/6),
which reviewed commit `e8e0e95171914127b7deb11bdcca47cbb3b5170d`.
The findings identify possible exposure paths; they do not establish an active
attack or disclosure of a real wallet key.

## Decisions

| Item | Disposition | Reason and implementation |
| --- | --- | --- |
| 1. Client representations | Fix | Ordinary configuration logging must not expose private keys or headers. Exclude these fields from dataclass representations, including inherited clients. Also exclude configured endpoint strings and bridge service API keys. Hide arbitrary nested `ChainClient.api_client` objects; verify the SDK aggregate and nested module clients. |
| 2. REST connection errors | Fix | Failed requests can expose credentials in URLs and underlying error messages. Report the method, sanitized origin, and exception type. Suppress the underlying exception chain in displayed tracebacks, including HTTP failures. Preserve structured API error codes and response data for callers. |
| 3. Native endpoint debug output | Fix | Debug output is often shared during troubleshooting. Apply the shared sanitizer to native HTTP RPC, EVM calls, and Substrate connection logs. HTTP RPC and Substrate connection failures also omit arbitrary lower-level error messages and suppress their displayed exception chains. |
| 4. Credentials in URL paths | Fix | An RPC path can be a credential. Both synchronous and asynchronous transports now share an origin-only sanitizer: scheme, hostname, and optional port. User information, paths, queries, and fragments are omitted. Invalid URLs use a fixed placeholder. Actual request URLs are unchanged. |
| 5. Gzip expansion | Fix | The compressed frame limit cannot bound expanded data. Read gzip data with an explicit output bound and reject oversized output before decoding JSON. Apply the same byte limit to plain binary and text JSON. |
| 6. Test keys | Fix and adopt policy below | Consolidate the reported literals into an explicitly public scalar-1 fixture for offline tests. Preserve the native signing regression vectors, which already used that scalar. Historical funding or use of the old keys cannot be established from repository history alone. |

## Test key provenance and policy

Both reported literals were introduced in the initial commit `cef8635`.
The bridge test labeled its key as an Anvil key; the other literal appeared in
seven signer test locations without a source attribution. Their current uses
are signing tests with mocked network operations. That does not prove that the
keys were never used or funded elsewhere. Replacing a committed key cannot
restore its secrecy.

`tests/_public_test_keys.py` now derives its key directly from the integer 1
and labels it as public. It has no claim to secrecy. Its address is
`0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf`.

- Offline unit tests may use this shared fixture with mocked network calls.
- An isolated local development chain may use public development keys. Never
  fund those addresses on shared networks or with assets of real value.
- Tests that submit remote transactions must require a dedicated test wallet
  from environment variables or ignored local configuration. CI must supply it
  through secret variables. Missing credentials must explicitly fail or skip;
  there must be no fallback to a public test key.
- Existing transaction scripts require `PRIVATE_KEY` before sending. Read-only
  scripts may use a zero-key placeholder because they do not sign or submit.
- Anyone who has used either old literal for a funded wallet must treat it as
  public and move assets and permissions to a fresh key. Removing a literal
  from the current tree does not remove it from Git history.

## WebSocket size limit

`WsClient(..., max_message_size=1_048_576)` limits each incoming WebSocket message
and each decoded JSON payload to 1 MiB by default. The limit counts UTF-8 bytes
for text and total expanded bytes across concatenated gzip members. A larger
positive byte limit may be supplied for applications that need larger messages.
Directly constructed `WsSession` objects accept the same keyword argument.
Oversized JSON payloads raise `ValueError` before JSON parsing. Malformed gzip
raises a decompression error instead of retrying it as plain JSON.

The raw `WsSession.recv()` method returns transport data; JSON decoding and the
expanded-data check apply to `recv_json()` and `recv_message()`.

## Verification and scope

`tests/test_security_boundaries.py` uses fake credential markers to check
representations, endpoint displays, native debug output, and formatted exception
tracebacks. It also covers plain and compressed JSON, exact byte boundaries,
concatenated gzip members, UTF-8 sizing, and oversized expansion before parsing.
Existing failover, REST/API errors, WebSocket subscriptions, and signing tests
provide compatibility coverage. No live transactions are required for this review.

Redaction covers SDK-generated representations and transport diagnostics.
Explicit access to credential fields and connection URLs remains available to
applications. Server-provided API error messages/details and JSON-RPC error
payloads remain application data and can contain arbitrary server content.
Traceback tools that capture local variables, application logging of raw
configuration, and third-party library logging require separate care.

The devnet review adds 51 focused cases. The Python 3.12 and 3.14 unit suites each pass
795 tests with one skip. The repository-wide 99% coverage gate has a pre-existing
shortfall: the unmodified devnet baseline measures 95.30%, while this change
measures 95.35%. The gate is unchanged; unrelated coverage gaps are outside this
security fix. All 57 changed executable lines in files measured by the coverage
configuration are exercised.
