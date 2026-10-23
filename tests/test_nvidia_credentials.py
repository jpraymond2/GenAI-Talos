"""Credential shape warnings for NVIDIA / Riva env vars."""

from __future__ import annotations

import warnings

import pytest

from recall.config import Settings, is_uuid, nvidia_credential_warnings


def test_is_uuid() -> None:
    assert is_uuid("1598d209-5e27-4d3c-8079-4751568b1081")
    assert not is_uuid("nvapi-abc")
    assert not is_uuid("")
    assert not is_uuid("1598d209")


def test_warn_when_api_key_missing_nvapi_prefix() -> None:
    settings = Settings(
        nvidia_api_key="not-a-real-key",
        riva_asr_function_id="1598d209-5e27-4d3c-8079-4751568b1081",
    )
    msgs = nvidia_credential_warnings(settings)
    assert any("nvapi-" in m and "NVIDIA_API_KEY" in m for m in msgs)


def test_warn_when_function_id_is_api_key() -> None:
    settings = Settings(
        nvidia_api_key="nvapi-testkey",
        riva_asr_function_id="nvapi-pasted-by-mistake",
    )
    msgs = nvidia_credential_warnings(settings)
    assert any("RIVA_ASR_FUNCTION_ID looks like an NVIDIA API key" in m for m in msgs)


def test_warn_when_function_id_not_uuid() -> None:
    settings = Settings(
        nvidia_api_key="nvapi-testkey",
        riva_asr_function_id="not-a-uuid",
    )
    msgs = nvidia_credential_warnings(settings)
    assert any("not a UUID" in m for m in msgs)


def test_warn_when_api_key_is_uuid() -> None:
    settings = Settings(
        nvidia_api_key="1598d209-5e27-4d3c-8079-4751568b1081",
        riva_asr_function_id="",
    )
    msgs = nvidia_credential_warnings(settings)
    assert any("looks like a UUID" in m for m in msgs)


def test_ok_shapes_produce_no_warnings() -> None:
    settings = Settings(
        nvidia_api_key="nvapi-testkey",
        riva_asr_function_id="1598d209-5e27-4d3c-8079-4751568b1081",
    )
    assert nvidia_credential_warnings(settings) == []


def test_settings_emits_userwarning_on_bad_shape() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Settings(
            nvidia_api_key="nvapi-ok",
            riva_asr_function_id="nvapi-wrong-slot",
        )
    assert any(issubclass(w.category, UserWarning) for w in caught)
