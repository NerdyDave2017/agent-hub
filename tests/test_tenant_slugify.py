"""Tenant slug derivation for sign-up and admin APIs."""

import re

from agent_hub_core.schemas.tenant import slug_from_workspace_name

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def test_slug_from_workspace_name_basic() -> None:
    s = slug_from_workspace_name("Acme Corp")
    assert s == "acme-corp"
    assert _SLUG.match(s)


def test_slug_from_workspace_name_special_chars() -> None:
    s = slug_from_workspace_name("My Team @ 2026!")
    assert s == "my-team-2026"
    assert _SLUG.match(s)


def test_slug_from_workspace_name_empty_fallback() -> None:
    s = slug_from_workspace_name("   !!!   ")
    assert s == "workspace"


def test_slug_truncation_max_length() -> None:
    long_name = "Word " * 80
    s = slug_from_workspace_name(long_name)
    assert len(s) <= 128
    assert _SLUG.match(s)
