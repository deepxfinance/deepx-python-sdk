"""Public test scalar for offline tests and isolated local chains only.

This is the integer 1, not a wallet secret. Never fund its address on a shared
network or import this fixture into a test that submits remote transactions.
"""

PUBLIC_TEST_PRIVATE_KEY = "0x" + (1).to_bytes(32, "big").hex()
PUBLIC_TEST_ADDRESS = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
