"""Product login credentials, encrypted at rest, keyed by product_id.

These are a Client's credentials for *their own product* -- what Playwright
types to get past the login screen before a demo starts. They are not Navigator
credentials and they never reach a browser: the plaintext password is returned
by exactly one method, `password_for`, which the demo runner calls server-side
at the moment of login.

The encryption key comes from NAVIGATOR_CREDENTIAL_KEY and there is no fallback.
A missing key raises rather than writing plaintext -- a vault that silently
degrades to storing secrets in the clear is worse than one that refuses to start.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from navigator.core.settings import settings


class CredentialVaultError(Exception):
    pass


class VaultNotConfigured(CredentialVaultError):
    """NAVIGATOR_CREDENTIAL_KEY is unset or unusable."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_logins (
    product_id          TEXT PRIMARY KEY,
    login_url           TEXT NOT NULL DEFAULT '',
    username            TEXT NOT NULL DEFAULT '',
    password_encrypted  BLOB NOT NULL,
    include_login_in_default_flow INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_provider_keys (
    product_id              TEXT PRIMARY KEY,
    gemini_key_encrypted    BLOB,
    groq_key_encrypted      BLOB,
    fish_key_encrypted      BLOB,
    updated_at              TEXT NOT NULL
);
"""

MISSING_KEY_MESSAGE = (
    "credential vault is not configured -- set NAVIGATOR_CREDENTIAL_KEY to a "
    "Fernet key (python -c \"from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())\")"
)


def _cipher() -> Fernet:
    key = (settings.credential_key or "").strip()
    if not key:
        raise VaultNotConfigured(MISSING_KEY_MESSAGE)
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise VaultNotConfigured(f"{MISSING_KEY_MESSAGE} ({exc})") from None


class CredentialVault:
    """One row per product. Same thread-local sqlite pattern as ActionLog."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn.executescript(_SCHEMA)
        columns = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(product_logins)")
        }
        if "include_login_in_default_flow" not in columns:
            self._conn.execute(
                "ALTER TABLE product_logins ADD COLUMN "
                "include_login_in_default_flow INTEGER NOT NULL DEFAULT 0"
            )

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    # -- write ---------------------------------------------------------------

    def put(
        self,
        product_id: str,
        *,
        login_url: str,
        username: str,
        password: str | None,
        include_login_in_default_flow: bool = False,
    ) -> None:
        """Save credentials. `password=None` keeps the stored one.

        None rather than "" for keep-existing: the dashboard never receives the
        real password back, so a save that only changes the username sends None
        and must not wipe the secret. An explicit "" does clear it.
        """
        if password is None:
            existing = self._row(product_id)
            if existing is None:
                raise CredentialVaultError(
                    f"no stored password for {product_id!r} to keep -- "
                    "send the password with the first save"
                )
            blob = existing["password_encrypted"]
        else:
            blob = _cipher().encrypt(password.encode())

        self._conn.execute(
            "INSERT INTO product_logins "
            "(product_id, login_url, username, password_encrypted, "
            "include_login_in_default_flow, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(product_id) DO UPDATE SET "
            "login_url=excluded.login_url, username=excluded.username, "
            "password_encrypted=excluded.password_encrypted, "
            "include_login_in_default_flow="
            "excluded.include_login_in_default_flow, "
            "updated_at=excluded.updated_at",
            (
                product_id,
                login_url.strip(),
                username.strip(),
                blob,
                int(include_login_in_default_flow),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def delete(self, product_id: str) -> None:
        self._conn.execute(
            "DELETE FROM product_logins WHERE product_id = ?", (product_id,)
        )

    # -- read ----------------------------------------------------------------

    def _row(self, product_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM product_logins WHERE product_id = ?", (product_id,)
        ).fetchone()

    def public(self, product_id: str) -> dict:
        """What the dashboard may see. Never the password.

        `has_password` is the only thing the browser learns about the secret --
        enough to render a masked placeholder and a Change action.
        """
        row = self._row(product_id)
        if row is None:
            return {
                "login_url": "",
                "username": "",
                "has_password": False,
                "include_login_in_default_flow": False,
                "updated_at": None,
            }
        return {
            "login_url": row["login_url"],
            "username": row["username"],
            "has_password": bool(row["password_encrypted"]),
            "include_login_in_default_flow": bool(
                row["include_login_in_default_flow"]
            ),
            "updated_at": row["updated_at"],
        }

    def login_url(self, product_id: str) -> str:
        """The configured login URL, or "" -- safe to call anywhere.

        Used by login detection, which must not require the vault key: knowing
        *where* a product logs in is not a secret, and detection has to keep
        working even on a host with no key configured.
        """
        row = self._row(product_id)
        return "" if row is None else row["login_url"]

    def include_login_in_default_flow(self, product_id: str) -> bool:
        """Per-product opt-in, Default flow only.

        Topic flows ignore this outright -- a Topic detour fires mid-demo, when
        the browser is already past login, so replaying a login there would log
        the End User out of the session they are watching.
        """
        row = self._row(product_id)
        return False if row is None else bool(row["include_login_in_default_flow"])

    def password_for(self, product_id: str) -> str | None:
        """Decrypt. Server-side only, at the moment Playwright needs it."""
        row = self._row(product_id)
        if row is None:
            return None
        try:
            return _cipher().decrypt(row["password_encrypted"]).decode()
        except InvalidToken:
            raise CredentialVaultError(
                f"stored password for {product_id!r} cannot be decrypted -- "
                "NAVIGATOR_CREDENTIAL_KEY has changed since it was saved"
            ) from None

    def credentials_for(self, product_id: str) -> tuple[str, str, str] | None:
        """(login_url, username, password), or None when nothing is stored."""
        row = self._row(product_id)
        if row is None:
            return None
        password = self.password_for(product_id)
        if password is None:
            return None
        return row["login_url"], row["username"], password

    # -- provider API keys (BYOK) --------------------------------------------

    def _provider_row(self, product_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM product_provider_keys WHERE product_id = ?",
            (product_id,),
        ).fetchone()

    def provider_keys_public(self, product_id: str) -> dict:
        row = self._provider_row(product_id)
        if row is None:
            return {
                "has_gemini_api_key": False,
                "has_groq_api_key": False,
                "has_fish_api_key": False,
                "updated_at": None,
            }
        return {
            "has_gemini_api_key": bool(row["gemini_key_encrypted"]),
            "has_groq_api_key": bool(row["groq_key_encrypted"]),
            "has_fish_api_key": bool(row["fish_key_encrypted"]),
            "updated_at": row["updated_at"],
        }

    def put_provider_keys(
        self,
        product_id: str,
        *,
        gemini_api_key: str | None = None,
        groq_api_key: str | None = None,
        fish_api_key: str | None = None,
    ) -> None:
        """None keeps stored key; \"\" clears it."""
        row = self._provider_row(product_id)
        gemini_blob = row["gemini_key_encrypted"] if row is not None else None
        groq_blob = row["groq_key_encrypted"] if row is not None else None
        fish_blob = row["fish_key_encrypted"] if row is not None else None

        if gemini_api_key is not None:
            gemini_blob = _cipher().encrypt(gemini_api_key.encode()) if gemini_api_key else None
        if groq_api_key is not None:
            groq_blob = _cipher().encrypt(groq_api_key.encode()) if groq_api_key else None
        if fish_api_key is not None:
            fish_blob = _cipher().encrypt(fish_api_key.encode()) if fish_api_key else None

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO product_provider_keys "
            "(product_id, gemini_key_encrypted, groq_key_encrypted, fish_key_encrypted, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(product_id) DO UPDATE SET "
            "gemini_key_encrypted=excluded.gemini_key_encrypted, "
            "groq_key_encrypted=excluded.groq_key_encrypted, "
            "fish_key_encrypted=excluded.fish_key_encrypted, "
            "updated_at=excluded.updated_at",
            (product_id, gemini_blob, groq_blob, fish_blob, now),
        )

    def provider_key(self, product_id: str, kind: str) -> str | None:
        row = self._provider_row(product_id)
        if row is None:
            return None
        col = {
            "gemini": "gemini_key_encrypted",
            "groq": "groq_key_encrypted",
            "fish": "fish_key_encrypted",
        }.get(kind)
        if col is None:
            return None
        blob = row[col]
        if not blob:
            return None
        try:
            return _cipher().decrypt(blob).decode()
        except InvalidToken:
            raise CredentialVaultError(
                f"stored {kind} key for {product_id!r} cannot be decrypted"
            ) from None

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> CredentialVault:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
