from app import create_app


def test_live_health():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "up"


def test_admin_requires_login():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/api/v1/admin/overview")

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTH_REQUIRED"
