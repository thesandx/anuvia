"""Room keys and player tokens.

Two different secrets live here and they are not interchangeable:

- A **room key** is public. A host reads it out to a group chat, so it is short,
  and the alphabet omits the characters people transcribe wrong.
- A **player token** is a credential. It is 32 random bytes, returned once, and
  only its SHA-256 hash is stored. It never appears in a room payload.

The split exists because the earlier design used one value for both. A player
id appears in `hostId`, in `bingo.turnOrder` and on every entry in `players`, so
any player in a room could read another player's id and act as them.
"""

import hashlib
import secrets
import unicodedata

#: `I`, `O`, `0` and `1` are excluded. Keys get spoken aloud and typed from
#: memory, and those four are the pairs people transcribe wrong.
ROOM_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_KEY_LENGTH = 6

#: 32 bytes, per the handover. base64url-encoded on the way out.
TOKEN_BYTES = 32


def create_room_key() -> str:
    """A six-character room key, e.g. `PLZ4K9`.

    About 1.07 billion combinations. The caller inserts and retries on a unique
    violation rather than pre-checking for existence — the unique index is the
    check, and a pre-check is a race.
    """
    return "".join(secrets.choice(ROOM_KEY_ALPHABET) for _ in range(ROOM_KEY_LENGTH))


def normalise_room_key(value: str) -> str:
    """Trims, uppercases, and strips the spaces and dashes people type."""
    return value.strip().upper().replace(" ", "").replace("-", "")


def is_valid_room_key(value: str) -> bool:
    """True when the input is a well-formed key. Does not check it exists."""
    key = normalise_room_key(value)
    return len(key) == ROOM_KEY_LENGTH and all(character in ROOM_KEY_ALPHABET for character in key)


def create_player_token() -> str:
    """A fresh player credential, base64url, returned to the client once."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    """The only form of a token that is ever stored."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def normalise_name(value: str) -> str:
    """Trims a nickname and normalises it to NFC before any comparison.

    Two visually identical nicknames can be different byte strings without this,
    which would let one player take another's apparent name.
    """
    return unicodedata.normalize("NFC", value).strip()


def initial_of(name: str) -> str:
    """First character of a nickname, uppercased.

    Indexes the string, not its bytes, so an astral character is not cut in
    half. Returns an empty string for blank input, which the client renders as a
    plain colour disc rather than a stray letter.
    """
    trimmed = name.strip()
    return trimmed[0].upper() if trimmed else ""
