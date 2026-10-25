from unittest.mock import Mock

import pytest
from pydantic import SecretStr

import agents
from schemas import LogicResult


def test_provider_structured_output_contract(monkeypatch):
    model = Mock()
    model.with_structured_output.return_value.invoke.return_value = LogicResult(findings=[])
    constructor = Mock(return_value=model)
    monkeypatch.setattr(agents, "ChatGoogleGenerativeAI", constructor)
    monkeypatch.setattr(agents.settings, "google_api_key", SecretStr("fake"))
    assert agents.review_logic([], [])["findings"] == []
    model.with_structured_output.assert_called_once_with(LogicResult, method="json_schema")
    assert constructor.call_args.kwargs["api_key"] == "fake"
    assert constructor.call_args.kwargs["vertexai"] is False
    assert constructor.call_args.kwargs["timeout"] > 0
    model.with_structured_output.return_value.invoke.return_value = None
    with pytest.raises(RuntimeError, match="invalid structured"):
        agents.review_logic([], [])


def test_missing_google_key_does_not_call_provider(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(agents, "ChatGoogleGenerativeAI", constructor)
    monkeypatch.setattr(agents.settings, "google_api_key", None)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        agents.review_logic([], [])
    constructor.assert_not_called()
