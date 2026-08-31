import pytest
import respx

from greenhouse_mcp.client import HARVEST_TOKEN_URL, GreenhouseClient


@pytest.fixture
def api_key():
    """v1 Harvest key — now only meaningful to the Job Board / Ingestion APIs."""
    return "test-api-key-12345"


@pytest.fixture
def client_credentials():
    return {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "user_id": "4321",
    }


def primed_client(**kwargs):
    """A v3 client holding a valid token, so no token route need be mocked.

    Endpoint tests are about the endpoint. The token exchange itself is covered
    explicitly in test_client.py, where it is the subject rather than setup.
    """
    import time

    client = GreenhouseClient(
        client_id="test-client-id",
        client_secret="test-client-secret",
        **kwargs,
    )
    client._token = "test-access-token"
    client._token_expires_at = time.monotonic() + 3600
    return client


@pytest.fixture
def client(client_credentials):
    return GreenhouseClient(**client_credentials)


@pytest.fixture
def board_client():
    return GreenhouseClient(board_token="test-board")


@pytest.fixture
def mock_api():
    with respx.mock(assert_all_called=False) as respx_mock:
        # Every Harvest v3 call now begins with a token fetch. Registering it here
        # keeps each test about the endpoint under test rather than about auth;
        # tests that care about token behaviour override this route.
        respx_mock.post(HARVEST_TOKEN_URL).mock(
            return_value=_token_response(),
        )
        yield respx_mock


def _token_response():
    import httpx

    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "access_token": "test-access-token",
            "expires_in": 3600,
        },
    )
