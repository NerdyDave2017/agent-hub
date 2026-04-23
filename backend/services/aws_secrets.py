"""AWS Secrets Manager helpers shared by integration OAuth flows."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3

from agent_hub_core.config.settings import get_settings


async def upsert_secret_string(*, name: str, secret_string: str) -> str:
    """Create or update a secret by **name**; returns the secret ARN."""

    def _sync() -> str:
        s = get_settings()
        sm_kw: dict[str, Any] = {"region_name": s.aws_region}
        if s.aws_endpoint_url:
            sm_kw["endpoint_url"] = s.aws_endpoint_url
        if s.aws_access_key_id and s.aws_secret_access_key:
            sm_kw["aws_access_key_id"] = s.aws_access_key_id
            sm_kw["aws_secret_access_key"] = s.aws_secret_access_key
        sm = boto3.client("secretsmanager", **sm_kw)
        try:
            sm.put_secret_value(SecretId=name, SecretString=secret_string)
            return str(sm.describe_secret(SecretId=name)["ARN"])
        except sm.exceptions.ResourceNotFoundException:
            out = sm.create_secret(Name=name, SecretString=secret_string)
            return str(out["ARN"])

    return await asyncio.to_thread(_sync)
