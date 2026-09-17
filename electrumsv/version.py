PACKAGE_VERSION = '0.2.0'                         # version of the client package
PACKAGE_DATE = '2026-09-17T12:00:00.000000-04:00'  # official timestamp for client package
# Negotiate protocol in this range
PROTOCOL_MIN = (1, 4)
PROTOCOL_MAX = (1, 4, 2)

# The hash of the mnemonic seed must begin with this
SEED_PREFIX      = '01'      # Standard wallet


def seed_prefix(seed_type):
    assert seed_type == 'standard'
    return SEED_PREFIX
