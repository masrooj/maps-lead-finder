"""Demo / upload storage: local disk or S3-compatible (optional boto3)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin


def backend() -> str:
    b = (os.getenv("STORAGE_BACKEND") or "").strip().lower()
    if b in ("s3", "r2", "minio"):
        return "s3"
    if os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET"):
        return "s3"
    return "local"


def uploads_root() -> Path:
    root = Path(os.getenv("UPLOADS_DIR") or "uploads")
    root.mkdir(parents=True, exist_ok=True)
    (root / "demos").mkdir(parents=True, exist_ok=True)
    return root


def public_base() -> str:
    return (os.getenv("UPLOADS_PUBLIC_BASE") or os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")


def save_bytes(relative_path: str, data: bytes, *, content_type: str = "application/octet-stream") -> dict:
    """Persist bytes; return {file_path, url, backend}."""
    rel = relative_path.lstrip("/")
    if backend() == "s3":
        url = _s3_put(rel, data, content_type=content_type)
        return {"file_path": rel, "url": url, "backend": "s3"}
    dest = uploads_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    base = public_base()
    url = f"{base}/uploads/{rel}" if base else f"/uploads/{rel}"
    return {"file_path": rel, "url": url, "backend": "local"}


def _s3_put(key: str, data: bytes, *, content_type: str) -> str:
    bucket = os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or ""
    if not bucket:
        raise RuntimeError("S3_BUCKET not set")
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 required for S3 storage — pip install boto3") from exc
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
    return {
        "backend": b,
        "uploads_dir": str(uploads_root()) if b == "local" else "",
        "s3_bucket": (os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or "") if b == "s3" else "",
        "public_base": public_base() or (os.getenv("S3_PUBLIC_BASE") or ""),
        "note": "On Vercel set STORAGE_BACKEND=s3 + S3_BUCKET (ephemeral FS cannot keep uploads).",
    }
