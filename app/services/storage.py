"""Demo / upload storage: local disk, Vercel Blob (preferred on Vercel), or optional S3."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin

import requests


def backend() -> str:
    """
    Resolve storage backend.
    Prefer explicit STORAGE_BACKEND; else Blob token → blob; else S3 bucket → s3; else local.
    Payment/Stripe keys are unrelated and never required for storage.
    """
    b = (os.getenv("STORAGE_BACKEND") or "").strip().lower()
    if b in ("blob", "vercel-blob", "vercel_blob"):
        return "blob"
    if b in ("s3", "r2", "minio"):
        return "s3"
    if b == "local":
        return "local"
    # Auto-detect (Vercel Blob preferred over S3 when token present)
    if os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN"):
        return "blob"
    if os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET"):
        return "s3"
    return "local"


def _default_uploads_dir() -> Path:
    explicit = (os.getenv("UPLOADS_DIR") or "").strip()
    return Path(explicit) if explicit else Path("uploads")


def uploads_root() -> Path:
    """
    Prefer the packaged ./uploads tree (committed with .gitkeep) so Vercel import
    does not need mkdir on a read-only deploy FS. Only mkdir when dirs are missing
    (local/Docker). Runtime demo bytes on Vercel should use Blob, not disk writes.
    """
    root = _default_uploads_dir()
    demos = root / "demos"
    if root.is_dir() and demos.is_dir():
        return root
    try:
        root.mkdir(parents=True, exist_ok=True)
        demos.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"uploads dir missing and not writable ({root}). "
            "Commit uploads/.gitkeep + uploads/demos/.gitkeep, or set UPLOADS_DIR."
        ) from exc
    return root


def public_base() -> str:
    return (os.getenv("UPLOADS_PUBLIC_BASE") or os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")


def _blob_token() -> str:
    return (os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN") or "").strip()


def save_bytes(relative_path: str, data: bytes, *, content_type: str = "application/octet-stream") -> dict:
    """Persist bytes; return {file_path, url, backend}."""
    rel = relative_path.lstrip("/")
    b = backend()
    if b == "blob":
        url = _blob_put(rel, data, content_type=content_type)
        return {"file_path": rel, "url": url, "backend": "blob"}
    if b == "s3":
        url = _s3_put(rel, data, content_type=content_type)
        return {"file_path": rel, "url": url, "backend": "s3"}
    dest = uploads_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    base = public_base()
    url = f"{base}/uploads/{rel}" if base else f"/uploads/{rel}"
    return {"file_path": rel, "url": url, "backend": "local"}


def _blob_put(key: str, data: bytes, *, content_type: str) -> str:
    """Upload via Vercel Blob REST API (no S3 required)."""
    token = _blob_token()
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN not set — create a Blob store in Vercel and copy the token")
    filename = quote(key, safe="/")
    resp = requests.put(
        f"https://vercel.com/api/blob?filename={filename}",
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-version": "7",
            "Content-Type": content_type or "application/octet-stream",
        },
        data=data,
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"Vercel Blob upload failed HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json() if resp.content else {}
    url = payload.get("url") or payload.get("downloadUrl")
    if not url:
        raise RuntimeError("Vercel Blob response missing url")
    return str(url)


def _s3_put(key: str, data: bytes, *, content_type: str) -> str:
    bucket = os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or ""
    if not bucket:
        raise RuntimeError("S3_BUCKET not set")
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 required for S3 storage — prefer STORAGE_BACKEND=blob on Vercel") from exc
    region = os.getenv("AWS_REGION") or os.getenv("S3_REGION") or "us-east-1"
    endpoint = os.getenv("S3_ENDPOINT_URL") or None
    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
    )
    extra = {"ContentType": content_type}
    acl = os.getenv("S3_ACL", "").strip()
    put_kwargs = {"Bucket": bucket, "Key": key, "Body": data, **extra}
    if acl:
        put_kwargs["ACL"] = acl
    client.put_object(**put_kwargs)
    public = os.getenv("S3_PUBLIC_BASE") or os.getenv("UPLOADS_PUBLIC_BASE") or ""
    if public:
        return urljoin(public.rstrip("/") + "/", key)
    if endpoint:
        return f"{endpoint.rstrip('/')}/{bucket}/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def storage_status() -> dict:
    b = backend()
    note = {
        "local": "Local disk / Docker volume. Fine for LOCAL; ephemeral on Vercel — use Blob.",
        "blob": "Vercel Blob (preferred on Vercel). Set BLOB_READ_WRITE_TOKEN.",
        "s3": "S3/R2 optional fallback. Prefer STORAGE_BACKEND=blob on Vercel.",
    }.get(b, "")
    return {
        "backend": b,
        "uploads_dir": str(uploads_root()) if b == "local" else "",
        "blob_configured": bool(_blob_token()) if b == "blob" else bool(_blob_token()),
        "s3_bucket": (os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or "") if b == "s3" else "",
        "public_base": public_base() or (os.getenv("S3_PUBLIC_BASE") or ""),
        "note": note,
    }
