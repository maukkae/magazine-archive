"""Shared remote Ollama OCR helpers for archive import pipelines."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

MIN_TEXT_CHARS = 50
DEFAULT_OLLAMA_HOST = ""
DEFAULT_OCR_MODEL = "glm-ocr:latest"
DEFAULT_CLEANUP_MODEL = "mistral-small:24b"


def normalize_ollama_host(value: str) -> str:
    host = (value or "").strip()
    if not host:
        return ""
    if not re.match(r"^https?://", host, re.IGNORECASE):
        host = f"http://{host}"
    return host.rstrip("/")


def _clean_text(text: str) -> str:
    cleaned = []
    for c in text:
        cp = ord(c)
        if 0x20 <= cp <= 0x7E or 0xC0 <= cp <= 0x24F or c in "\n\t":
            cleaned.append(c)
        else:
            cleaned.append(" ")
    text = "".join(cleaned)
    text = re.sub(r"([A-Za-zÅÄÖåäö]+)\s*-\s+([A-Za-zÅÄÖåäö]+)", r"\1\2", text)
    return " ".join(text.split())


def _cleanup_meta_response(raw: str, cleaned: str) -> str:
    if re.search(
        r"(provided ocr text|does not contain any finnish|if you have more text|please provide it|based on the rules specified)",
        cleaned or "",
        re.IGNORECASE,
    ):
        return raw
    return cleaned


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def get_ollama_env_settings() -> dict:
    return {
        "host": normalize_ollama_host(os.environ.get("ARCHIVE_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)),
        "ocr_model": os.environ.get("ARCHIVE_OCR_MODEL", DEFAULT_OCR_MODEL).strip() or DEFAULT_OCR_MODEL,
        "cleanup_model": os.environ.get("ARCHIVE_CLEANUP_MODEL", DEFAULT_CLEANUP_MODEL).strip() or DEFAULT_CLEANUP_MODEL,
        "use_cleanup": _bool_env("ARCHIVE_OLLAMA_USE_CLEANUP", True),
    }


def ollama_enabled_from_env() -> bool:
    return bool(get_ollama_env_settings()["host"])


def ollama_test_connection(host: str, ocr_model: str | None = None, cleanup_model: str | None = None) -> dict:
    base = normalize_ollama_host(host)
    if not base:
        return {"ok": False, "error": "missing host"}
    req = urllib.request.Request(
        f"{base}/api/tags",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    models = []
    for item in payload.get("models", []):
        name = item.get("name")
        if name:
            models.append(name)

    missing = []
    for model in [ocr_model, cleanup_model]:
        if model and model not in models:
            missing.append(model)

    return {
        "ok": True,
        "host": base,
        "models": models,
        "missing_models": missing,
    }


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _call_ollama(base: str, model: str, prompt: str, image_path: Path | None = None, timeout: int = 120) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    if image_path:
        payload["images"] = [_encode_image(image_path)]
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data.get("response", "")).strip()


def ocr_pages_with_ollama(
    page_files: list[Path],
    mag: str,
    year: str,
    issue: str,
    *,
    skip_cover: bool = True,
    min_chars: int = MIN_TEXT_CHARS,
) -> list[dict]:
    settings = get_ollama_env_settings()
    host = settings["host"]
    if not host:
        return []

    ocr_model = settings["ocr_model"]
    cleanup_model = settings["cleanup_model"]
    use_cleanup = settings["use_cleanup"]

    print(f"    remote Ollama OCR via {host} ({ocr_model})...")
    raw_pages: list[tuple[int, str]] = []
    for page_path in sorted(page_files):
        m = re.search(r"_(\d{3})\.jpe?g$", page_path.name, re.IGNORECASE)
        if not m:
            continue
        page_num = int(m.group(1))
        if skip_cover and page_num == 1:
            continue
        print(f"    page {page_num:03d}: vision OCR...", end=" ", flush=True)
        try:
            raw = _call_ollama(
                host,
                ocr_model,
                "Extract all visible text from this magazine page image. Return only the text, preserving paragraphs. No commentary.",
                page_path,
                timeout=120,
            )
        except Exception as e:
            print(f"failed ({e})")
            continue
        raw = _clean_text(raw)
        print(f"{len(raw)} chars")
        raw_pages.append((page_num, raw))

    if use_cleanup and raw_pages:
        print(f"    remote Mistral cleanup via {cleanup_model}...")
        cleaned_pages: list[tuple[int, str]] = []
        for page_num, raw in raw_pages:
            print(f"    page {page_num:03d}: cleanup...", end=" ", flush=True)
            try:
                cleaned = _call_ollama(
                    host,
                    cleanup_model,
                    "Clean up this OCR text from a scanned Finnish magazine page.\n"
                    "- Join words split by line-break hyphens.\n"
                    "- Fix obvious OCR character confusions.\n"
                    "- Preserve Finnish characters and paragraph structure.\n"
                    "- Do not add or remove content.\n"
                    "- If no cleanup is needed, return the OCR text unchanged.\n"
                    "- Return only the cleaned text. No commentary, no explanations, no quotes.\n\n"
                    f"OCR text:\n{raw}",
                    None,
                    timeout=120,
                )
            except Exception as e:
                print(f"failed ({e})")
                cleaned = raw
            cleaned = _cleanup_meta_response(raw, cleaned or raw)
            cleaned = _clean_text(cleaned or raw)
            print(f"{len(cleaned)} chars")
            cleaned_pages.append((page_num, cleaned))
        raw_pages = cleaned_pages

    entries = []
    for page_num, text in raw_pages:
        if len(text) >= min_chars:
            entries.append(
                {
                    "mag": mag,
                    "year": year,
                    "issue": issue,
                    "page": page_num,
                    "text": text,
                }
            )
        else:
            print(f"    page {page_num:03d}: no usable text")
    return entries
