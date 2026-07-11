"""Diagnostic CLI: runs the pipeline on a file or text and prints the result, bypassing Telegram."""

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from app import llm, pipeline
from app.config import load_config
from app.llm import Source
from app.qr import NBU_QR_PREFIX

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _build_source(args: argparse.Namespace) -> Source:
    if args.text is not None:
        return Source(kind="text", text=args.text)
    path = Path(args.file)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return Source(kind="pdf", data=path.read_bytes())
    if suffix in _MEDIA_TYPES:
        return Source(kind="image", data=path.read_bytes(), media_type=_MEDIA_TYPES[suffix])
    sys.exit(f"Unsupported file type: {suffix} (expected .pdf/.png/.jpg/.jpeg)")


def _decoded_payload_lines(url: str) -> list[str]:
    b64 = url.removeprefix(NBU_QR_PREFIX)
    payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("cp1251")
    return payload.split("\n")


async def _run(args: argparse.Namespace) -> None:
    llm.init(load_config(model_override=args.model))
    result = await pipeline.process(_build_source(args))

    if result.requisites is not None:
        print("Extracted requisites:")
        for field, value in result.requisites.model_dump().items():
            print(f"  {field}: {value!r}")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    if not result.ok:
        sys.exit(f"Error: {result.error}")

    print("\nQR payload lines:")
    for index, line in enumerate(_decoded_payload_lines(result.qr.url)):
        print(f"  {index:2}: {line!r}")
    print(f"\nURL: {result.qr.url}")
    if args.qr_out:
        Path(args.qr_out).write_bytes(result.qr.png)
        print(f"QR PNG saved to {args.qr_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the requisites pipeline on a file or text")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("file", nargs="?", help="Path to a PDF/PNG/JPEG file")
    input_group.add_argument("--text", help="Plain text with requisites")
    parser.add_argument("--model", choices=("haiku", "sonnet", "opus"),
                        help="Extraction/validation model (overrides RAHUNOK_QR_BOT_MODEL)")
    parser.add_argument("--qr-out", help="Where to save the QR PNG")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
