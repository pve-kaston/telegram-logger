#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _load_key(key_b64_arg: str | None) -> bytes:
    raw = key_b64_arg or os.getenv("TELEGRAM_DELETED_MEDIA_KEY_B64", "")
    if not raw:
        raise ValueError("Missing key: provide --key-b64 or set TELEGRAM_DELETED_MEDIA_KEY_B64")
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

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decrypt media_deleted *.enc files produced by telegram_logger EncryptedDeletedStorage"
    )
    parser.add_argument("--enc", required=True, help="Path to encrypted .enc file")
    parser.add_argument("--out", help="Output path for decrypted file (default: remove .enc suffix)")
    parser.add_argument("--key-b64", help="Base64 AES key (fallback: DELETED_MEDIA_KEY_B64 env var)")
    args = parser.parse_args()

    enc_path = Path(args.enc)
    if not enc_path.exists():
        print(f"ERROR: File not found: {enc_path}", file=sys.stderr)
        return 2

    def _default_filename(enc_path: Path) -> str:
        if enc_path.suffix == ".enc":
            return enc_path.with_suffix("").name
        return enc_path.name + ".dec"

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / _default_filename(enc_path)
    else:
        out_path = enc_path.parent / _default_filename(enc_path)

    if out_path.exists() and not args.force:
        print(f"ERROR: Output exists: {out_path}", file=sys.stderr)
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

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
