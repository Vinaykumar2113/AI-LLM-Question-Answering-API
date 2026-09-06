import os
os.environ["JWT_SECRET"] = "test-secret"
os.environ["SKIP_DB_INIT"] = "true"

from fastapi.testclient import TestClient
from app.main import app


def test_login_success():
    with TestClient(app) as client:
        response = client.post("/auth/login", data={"username": "user", "password": "User@123"})
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["role"] == "user"
        assert body["access_token"]


def test_login_failure():
    with TestClient(app) as client:
        response = client.post("/auth/login", data={"username": "user", "password": "wrong"})
        assert response.status_code == 401


def test_chat_requires_authentication():
    with TestClient(app) as client:
        response = client.post("/chat", json={"question": "Hello"})
        assert response.status_code == 401
