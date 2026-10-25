from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_workflow import graph

from core.config import settings
from main import create_app


def test_review_status_approval_and_conflict(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", False)
    monkeypatch.setattr(settings, "api_token", None)
    with TestClient(create_app(graph())) as client:
        response = client.post("/review", json={"repo": "owner/repo", "pr_number": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "awaiting_approval"
        url = f"/reviews/{body['review_id']}"
        assert "files" not in client.get(url).json()
        assert (
            client.post(url + "/approval", json={"approved": False}).json()["status"] == "rejected"
        )
        assert client.post(url + "/approval", json={"approved": True}).status_code == 409
        assert client.post(url + "/resume").status_code == 409
        assert client.get("/reviews/not-an-id").status_code == 404


def test_auth_and_validation(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", False)
    monkeypatch.setattr(settings, "api_token", SecretStr("test-secret"))
    with TestClient(create_app(graph())) as client:
        assert client.post("/review", json={"repo": "a/b", "pr_number": 1}).status_code == 401
        assert (
            client.post(
                "/review",
                headers={"Authorization": "Bearer test-secret"},
                json={"repo": "../a/b", "pr_number": 0},
            ).status_code
            == 422
        )
