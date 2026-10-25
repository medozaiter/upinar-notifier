import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .paths import ENV_FILE


def normalize_fernet_secret(secret: str) -> str:
    value = (secret or "").strip()
    if not value:
        raise RuntimeError("ENCRYPTION_KEY is empty.")
    try:
        Fernet(value.encode("utf-8"))
        return value
    except Exception:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii")


def ensure_fernet_key(existing: str) -> str:
    value = (existing or "").strip()
    if value:
        return value
    generated = Fernet.generate_key().decode("ascii")
    _persist_env_value("ENCRYPTION_KEY", generated)
    os.environ["ENCRYPTION_KEY"] = generated
    print("[Security] Generated ENCRYPTION_KEY and saved it to .env")
    return generated


def _persist_env_value(name: str, value: str) -> None:
    lines: list[str] = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()

    replaced = False
    updated = []
    for line in lines:
        if line.startswith(f"{name}="):
            updated.append(f"{name}={value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(f"{name}={value}")

    with open(ENV_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(updated).rstrip() + "\n")


@lru_cache(maxsize=4)
def _fernet(secret: str) -> Fernet:
    return Fernet(normalize_fernet_secret(secret).encode("utf-8"))


def encrypt_text(plaintext: str, secret: str) -> str:
    plaintext = str(plaintext or "")

    if len(plaintext) > 100_000:
        raise ValueError(
            "plaintext too large for encryption"
        )

    token = _fernet(secret).encrypt(
        plaintext.encode("utf-8")
    )

    return token.decode("ascii")


def decrypt_text(ciphertext: str, secret: str) -> str:

    ciphertext = str(ciphertext or "")

    if len(ciphertext) > 200_000:
        raise ValueError(
            "ciphertext too large"
        )

    try:
        return _fernet(secret).decrypt(
            ciphertext.encode("utf-8")
        ).decode("utf-8")

    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt stored credentials. "
            "ENCRYPTION_KEY does not match the database."
        ) from exc
