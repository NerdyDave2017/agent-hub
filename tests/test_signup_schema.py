"""Sign-up request validation (no database)."""

import pytest
from pydantic import ValidationError

from agent_hub_core.schemas.auth import SignupRequest


def test_signup_passwords_must_match() -> None:
    with pytest.raises(ValidationError) as exc:
        SignupRequest(
            name="Ada",
            email="ada@example.com",
            workspace_name="Lovelace Labs",
            password="hunter2hunter2",
            password_confirm="different",
        )
    assert "password" in str(exc.value).lower()
