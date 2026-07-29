from pathlib import Path

from fastapi.testclient import TestClient

from catgirl.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data"))


def test_first_run_setup_locks_management_api_and_login_restores_access(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/api/auth/status").json() == {
            "setup_required": True,
            "authenticated": False,
            "username": "",
        }
        locked = client.get("/api/overview")
        assert locked.status_code == 428
        assert locked.json()["detail"] == "请先创建管理员账号"

        setup = client.post(
            "/api/auth/setup",
            json={"username": "管理员", "password": "correct-horse-battery"},
        )
        assert setup.status_code == 201, setup.text
        assert setup.json() == {
            "setup_required": False,
            "authenticated": True,
            "username": "管理员",
        }
        assert client.get("/api/overview").status_code == 200
        assert client.post(
            "/api/auth/setup",
            json={"username": "other", "password": "another-password"},
        ).status_code == 409

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/overview").status_code == 401
        assert client.get("/health").status_code == 200

        wrong = client.post(
            "/api/auth/login",
            json={"username": "管理员", "password": "wrong-password"},
        )
        assert wrong.status_code == 401
        assert client.get("/api/overview").status_code == 401

        login = client.post(
            "/api/auth/login",
            json={"username": "管理员", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["authenticated"] is True
        assert client.get("/api/overview").status_code == 200


def test_auth_validates_credentials(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.post(
            "/api/auth/setup",
            json={"username": "", "password": "long-enough-password"},
        ).status_code == 422
        assert client.post(
            "/api/auth/setup",
            json={"username": "admin", "password": "short"},
        ).status_code == 422
