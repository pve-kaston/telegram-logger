#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _load_key(key_b64_arg: str | None) -> bytes:
    raw = key_b64_arg or os.getenv("DELETED_MEDIA_KEY_B64", "")
    if not raw:
        raise ValueError("Missing key: provide --key-b64 or set DELETED_MEDIA_KEY_B64")
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise ValueError(f"Invalid base64 key: {exc}") from exc
    if len(key) != 32:
        raise ValueError("Key must decode to exactly 32 bytes (AES-256-GCM)")
    return key


def _decrypt(enc_path: Path, key: bytes) -> bytes:
    blob = enc_path.read_bytes()
    if len(blob) < 13:
        raise ValueError("Encrypted file is too short")
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ct, None)


def _verify_sha(enc_path: Path, plaintext: bytes, sha_path_arg: str | None) -> bool | None:
    sha_path = Path(sha_path_arg) if sha_path_arg else Path(str(enc_path) + ".sha256")
    if not sha_path.exists():
        return None
    expected = sha_path.read_text(encoding="utf-8").strip().lower()
    actual = hashlib.sha256(plaintext).hexdigest().lower()
    return expected == actual


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decrypt media_deleted *.enc files produced by telegram_logger EncryptedDeletedStorage"
    )
    parser.add_argument("--enc", required=True, help="Path to encrypted .enc file")
    parser.add_argument("--out", help="Output path for decrypted file (default: remove .enc suffix)")
    parser.add_argument("--key-b64", help="Base64 AES key (fallback: DELETED_MEDIA_KEY_B64 env var)")
    parser.add_argument(
        "--verify-sha",
        action="store_true",
        help="Verify plaintext SHA-256 against <enc>.sha256 (or --sha-path)",
    )
    parser.add_argument("--sha-path", help="Custom path to sha256 file")
    parser.add_argument("--force", action="store_true", help="Overwrite output file if exists")
    args = parser.parse_args()

    enc_path = Path(args.enc)
    if not enc_path.exists():
        print(f"ERROR: File not found: {enc_path}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else Path(str(enc_path)[:-4] if str(enc_path).endswith(".enc") else str(enc_path) + ".dec")
    if out_path.exists() and not args.force:
        print(f"ERROR: Output exists: {out_path} (use --force)", file=sys.stderr)
        return 2

    try:
        key = _load_key(args.key_b64)
        plaintext = _decrypt(enc_path, key)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(plaintext)
    print(f"Decrypted: {enc_path} -> {out_path}")

    if args.verify_sha:
        result = _verify_sha(enc_path, plaintext, args.sha_path)
        if result is True:
            print("SHA256: OK")
        elif result is False:
            print("SHA256: MISMATCH", file=sys.stderr)
            return 3
        else:
            print("SHA256: file not found (skipped)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
