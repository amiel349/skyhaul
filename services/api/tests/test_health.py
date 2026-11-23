from skyhaul_api import create_app


def test_healthz_returns_ok() -> None:
    client = create_app().test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
