import hashlib
import hmac
import secrets

from app.config import get_settings

# Crockford base32: no I, L, O or U, so a code can't be misread when copied by hand.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 16  # 16 characters x 5 bits each = 80 bits of randomness
PREFIX = "WD"

_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def display(code: str) -> str:
    groups = [code[i : i + 4] for i in range(0, LENGTH, 4)]
    return "-".join([PREFIX, *groups])


def normalise(raw: str) -> str | None:
    """Turn what the reporter typed into the bare 16-character code, or None if it can't be one."""
    code = "".join(raw.split()).replace("-", "").upper().translate(_LOOKALIKES)
    if len(code) == len(PREFIX) + LENGTH and code.startswith(PREFIX):
        code = code[len(PREFIX) :]
    if len(code) != LENGTH or any(char not in ALPHABET for char in code):
        return None
    return code


def hash_code(code: str) -> str:
    secret = get_settings().case_code_secret.encode()
    return hmac.new(secret, code.encode(), hashlib.sha256).hexdigest()
