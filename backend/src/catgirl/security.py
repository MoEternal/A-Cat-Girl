from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key_path: Path):
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
        self._fernet = Fernet(key_path.read_bytes().strip())

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("API 密钥无法解密，请检查 data/secret.key") from exc

    def masked(self, encrypted_value: str) -> str:
        value = self.decrypt(encrypted_value)
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:3]}{'*' * min(12, len(value) - 6)}{value[-3:]}"
