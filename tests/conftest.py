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


HARVEST_BASE = "https://harvest.greenhouse.io/v3"


def mock_v3_side_calls(*, attachments=None, stages=None, applications=None):
    """Register the extra endpoints Harvest v3 split out of inline fields.

    v1 returned a candidate's attachments and an application's stage inline, so
    no fixture ever had to mock them. Call this at the top of a test body (the
    `@respx.mock` decorator clears anything registered outside it), and pass
    real payloads only for the ones the test actually asserts on.

    `applications` is separate because many tests already mock that path; leave
    it None when they do, so the test's own route stays authoritative.
    """
    import httpx
    import respx

    respx.get(f"{HARVEST_BASE}/attachments").mock(
        return_value=httpx.Response(200, json=attachments or [])
    )
    respx.get(f"{HARVEST_BASE}/job_interview_stages").mock(
        return_value=httpx.Response(200, json=stages or [])
    )
    if applications is not None:
        respx.get(f"{HARVEST_BASE}/applications").mock(
            return_value=httpx.Response(200, json=applications)
        )


def mock_resume_chain(candidates, *, pipeline_applications=None, stages=None):
    """Serve v3's candidate → applications → attachments chain.

    Fixtures still carry `attachments` inline on the candidate, which is how v1
    returned them and how they read most clearly. This derives the two extra
    endpoints v3 requires from that same data, so a test states its candidates
    once and does not have to hand-maintain three consistent payloads.

    Verified against a live instance: `/attachments?candidate_ids=` returns that
    candidate's attachments directly, so no application hop is involved.
    """
    import httpx
    import respx

    atts_for = {c["id"]: c.get("attachments", []) for c in candidates if "id" in c}

    def _applications(request):
        return httpx.Response(200, json=pipeline_applications or [])

    def _attachments(request):
        # v3 filters attachments by candidate directly (plural `candidate_ids`).
        raw = request.url.params.get("candidate_ids")
        return httpx.Response(200, json=atts_for.get(int(raw), []) if raw else [])

    respx.get(f"{HARVEST_BASE}/applications").mock(side_effect=_applications)
    respx.get(f"{HARVEST_BASE}/attachments").mock(side_effect=_attachments)
    respx.get(f"{HARVEST_BASE}/job_interview_stages").mock(
        return_value=httpx.Response(200, json=stages or [])
    )


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
