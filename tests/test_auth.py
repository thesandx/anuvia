import pytest


@pytest.mark.asyncio
async def test_register(client):
    response = await client.post(
        "/auth/register",
        json={"email": "test@example.com", "password": "secret123", "full_name": "Test User"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_login(client):
    await client.post(
        "/auth/register",
        json={"email": "test@example.com", "password": "secret123"},
    )
    response = await client.post(
        "/auth/login",
        json={"email": "test@example.com", "password": "secret123"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_me(client):
    await client.post(
        "/auth/register",
        json={"email": "me@example.com", "password": "secret123"},
    )
    login = await client.post(
        "/auth/login",
        json={"email": "me@example.com", "password": "secret123"},
    )
    token = login.json()["access_token"]
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
