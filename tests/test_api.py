"""Tests for the Vinted Go account API client."""
import aiohttp
import pytest

from custom_components.vinted_go.api import (
    VintedGoApiClient,
    VintedGoApiError,
    VintedGoAuthError,
    VintedGoInvalidToken,
)
from custom_components.vinted_go.const import (
    CONFIRM_URL,
    LOGOUT_URL,
    ME_URL,
    REFRESH_URL,
    REGISTER_URL,
    SHIPMENTS_URL,
    TRACKING_EVENTS_URL,
)

from .payloads import ROTATED_TOKENS, TOKENS


class _Resp:
    def __init__(self, status: int, body: object = None) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Routes get/post/delete to queued responses keyed by (method, url)."""

    def __init__(self, routes: dict) -> None:
        self._routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, str]] = []

    def _pop(self, method: str, url: str):
        self.calls.append((method, url))
        queue = self._routes.get((method, url))
        if not queue:
            raise AssertionError(f"unexpected {method} {url}")
        item = queue.pop(0)
        if isinstance(item, aiohttp.ClientError):
            raise item
        return _Resp(*item)

    def get(self, url, headers=None):
        return self._pop("get", url)

    def post(self, url, json=None):
        return self._pop("post", url)

    def delete(self, url, json=None):
        return self._pop("delete", url)


# --- register / confirm -----------------------------------------------------


async def test_register_ok():
    session = _Session({("post", REGISTER_URL): [(200, {})]})
    await VintedGoApiClient(session).async_register("a@b.c")
    assert ("post", REGISTER_URL) in session.calls


async def test_register_error_raises():
    session = _Session({("post", REGISTER_URL): [(500, {})]})
    with pytest.raises(VintedGoApiError):
        await VintedGoApiClient(session).async_register("a@b.c")


async def test_confirm_stores_tokens_and_reports_rotation():
    rotated: list[str] = []
    session = _Session({("post", CONFIRM_URL): [(200, TOKENS)]})
    client = VintedGoApiClient(session, on_tokens_updated=rotated.append)
    await client.async_confirm("tok")
    assert client.refresh_token == TOKENS["refresh_token"]
    assert rotated == [TOKENS["refresh_token"]]


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_confirm_bad_token_raises_invalid(status):
    session = _Session({("post", CONFIRM_URL): [(status, {})]})
    with pytest.raises(VintedGoInvalidToken):
        await VintedGoApiClient(session).async_confirm("tok")


async def test_confirm_other_status_raises_api_error():
    session = _Session({("post", CONFIRM_URL): [(503, {})]})
    with pytest.raises(VintedGoApiError):
        await VintedGoApiClient(session).async_confirm("tok")


async def test_confirm_missing_token_field_raises():
    session = _Session({("post", CONFIRM_URL): [(200, {"session_token": "x"})]})
    with pytest.raises(VintedGoApiError):
        await VintedGoApiClient(session).async_confirm("tok")


async def test_confirm_unparseable_body_raises():
    session = _Session({("post", CONFIRM_URL): [(200, ValueError("nope"))]})
    with pytest.raises(VintedGoApiError):
        await VintedGoApiClient(session).async_confirm("tok")


async def test_get_user_id_missing_field_raises():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS)],
            ("get", ME_URL): [(200, {"no_user_id": True})],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoApiError):
        await client.async_get_user_id()


async def test_refresh_unexpected_status_raises_api_error():
    session = _Session({("post", REFRESH_URL): [(500, {})]})
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoApiError):
        await client.async_get_shipments()


# --- session refresh --------------------------------------------------------


async def test_get_user_id_refreshes_first():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS)],
            ("get", ME_URL): [(200, {"user_id": 12345})],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    assert await client.async_get_user_id() == 12345


async def test_refresh_failure_raises_auth_error():
    session = _Session({("post", REFRESH_URL): [(401, {})]})
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoAuthError):
        await client.async_get_shipments()


async def test_refresh_without_token_raises_auth_error():
    client = VintedGoApiClient(_Session({}), refresh_token=None)
    with pytest.raises(VintedGoAuthError):
        await client.async_get_shipments()


async def test_refresh_network_error_is_api_error():
    session = _Session({("post", REFRESH_URL): [aiohttp.ClientError()]})
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoApiError):
        await client.async_get_shipments()


async def test_401_triggers_one_refresh_and_retry():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS), (200, ROTATED_TOKENS)],
            ("get", SHIPMENTS_URL): [(401, {}), (200, [])],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    assert await client.async_get_shipments() == []
    # refreshed once at start (no token) and once on the 401
    assert client.refresh_token == ROTATED_TOKENS["refresh_token"]


async def test_persistent_401_raises_auth_error():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS), (200, ROTATED_TOKENS)],
            ("get", SHIPMENTS_URL): [(401, {}), (401, {})],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoAuthError):
        await client.async_get_shipments()


# --- shipments / timeline ---------------------------------------------------


async def test_get_shipments_returns_dicts_only():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS)],
            ("get", SHIPMENTS_URL): [(200, [{"tracking_code": "A"}, "junk"])],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    assert await client.async_get_shipments() == [{"tracking_code": "A"}]


async def test_get_shipments_non_list_raises():
    session = _Session(
        {
            ("post", REFRESH_URL): [(200, TOKENS)],
            ("get", SHIPMENTS_URL): [(200, {"nope": True})],
        }
    )
    client = VintedGoApiClient(session, refresh_token="rt")
    with pytest.raises(VintedGoApiError):
        await client.async_get_shipments()


async def test_get_tracking_events_ok_404_and_error():
    code = "VGS1"
    url = TRACKING_EVENTS_URL.format(tracking_code=code)
    body = {"tracking_events": []}
    client = VintedGoApiClient(_Session({("get", url): [(200, body)]}))
    assert await client.async_get_tracking_events(code) == body
    client = VintedGoApiClient(_Session({("get", url): [(404, {})]}))
    assert await client.async_get_tracking_events(code) is None
    client = VintedGoApiClient(_Session({("get", url): [(500, {})]}))
    assert await client.async_get_tracking_events(code) is None
    client = VintedGoApiClient(_Session({("get", url): [aiohttp.ClientError()]}))
    assert await client.async_get_tracking_events(code) is None


# --- logout -----------------------------------------------------------------


async def test_logout_best_effort():
    session = _Session({("delete", LOGOUT_URL): [(200, {})]})
    client = VintedGoApiClient(session, refresh_token="rt")
    await client.async_logout()
    assert ("delete", LOGOUT_URL) in session.calls


async def test_logout_without_token_is_noop():
    session = _Session({})
    await VintedGoApiClient(session).async_logout()
    assert session.calls == []


async def test_logout_swallows_network_error():
    session = _Session({("delete", LOGOUT_URL): [aiohttp.ClientError()]})
    await VintedGoApiClient(session, refresh_token="rt").async_logout()
