import os
os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient
from app.main import app


def test_login_success():
    with TestClient(app) as client:
        response = client.post("/auth/login", data={"username": "user", "password": "User@123"})
        assert response.status_code == 200
        assert response.json()["token_type"] == "bearer"


def test_login_failure():
    with TestClient(app) as client:
        response = client.post("/auth/login", data={"username": "user", "password": "wrong"})
        assert response.status_code == 401
