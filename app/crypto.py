from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    pass


def encrypt(key: str, text: str) -> bytes:
    try:
        return Fernet(key.encode()).encrypt(text.encode())
    except (ValueError, TypeError):
        raise CryptoError("неверный SESSION_KEY") from None


def decrypt(key: str, blob: bytes) -> str:
    try:
        return Fernet(key.encode()).decrypt(blob).decode()
    except (InvalidToken, ValueError, TypeError):
        raise CryptoError("не удалось расшифровать") from None
