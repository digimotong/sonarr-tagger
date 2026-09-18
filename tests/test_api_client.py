"""Tests for the SonarrAPI HTTP client.

The headline guarantee here is that *every* HTTP call carries a timeout. Without
one, a half-open connection wedges the poll loop forever - the process stays alive
and healthy-looking while doing nothing, which is far worse than a crash.
"""

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, RequestException

import main
from conftest import FakeResponse, FakeSession

BASE_URL = 'http://sonarr:8989'
API_KEY = 'test-key'

# (method name on the client, HTTP verb it must issue, kwargs identifying the call)
ENDPOINT_CASES = [
    ('get_shows', 'get'),
    ('get_tags', 'get'),
    ('get_episode_files', 'get'),
    ('get_episodes', 'get'),
    ('update_show', 'put'),
    ('update_episode', 'put'),
]

def _make_client(responses):
    """Build a client wired to a FakeSession."""
    session = FakeSession(responses)
    return main.SonarrAPI(BASE_URL, API_KEY, session=session), session

def _empty_response():
    """Response payload that satisfies every method, so no TypeError is raised."""
    return FakeResponse([])

class TestSessionInjection:
    """The injectable session is what makes the client testable at all."""

    def test_injected_session_is_used(self):
        """The supplied session is the one that issues requests."""
        client, session = _make_client({'get': FakeResponse([])})
        client.get_shows()
        assert len(session.calls) == 1

    def test_default_session_is_created_when_omitted(self):
        """Production behaviour is unchanged: no session means a real one."""
        client = main.SonarrAPI(BASE_URL, API_KEY)
        assert client.session is not None
        assert hasattr(client.session, 'get')

    def test_api_key_header_is_set_on_injected_session(self):
        """Auth header is applied even to an injected session."""
        _, session = _make_client({'get': FakeResponse([])})
        assert session.headers['X-Api-Key'] == API_KEY
        assert session.headers['Accept'] == 'application/json'

    def test_api_key_header_is_case_insensitive(self):
        """requests' headers are case-insensitive; the fake mirrors that."""
        _, session = _make_client({'get': FakeResponse([])})
        assert session.headers['x-api-key'] == API_KEY

    @pytest.mark.parametrize('url', [
        'http://sonarr:8989',
        'http://sonarr:8989/',
        'http://sonarr:8989///',
    ])
    def test_trailing_slashes_are_normalised(self, url):
        """A trailing slash must not produce a doubled slash in the path."""
        client = main.SonarrAPI(url, API_KEY, session=FakeSession(
            {'get': FakeResponse([])}))
        assert client.base_url == 'http://sonarr:8989'

class TestTimeouts:
    """Every request must pass timeout=REQUEST_TIMEOUT."""

    @pytest.mark.parametrize('method_name,verb', ENDPOINT_CASES)
    def test_every_request_passes_a_timeout(self, method_name, verb):
        """No HTTP call may be issued without a timeout."""
        client, session = _make_client({verb: _empty_response()})
        method = getattr(client, method_name)
        if method_name == 'get_episode_files':
            method(1)
        elif method_name == 'get_episodes':
            method(1)
        elif method_name == 'update_show':
            method(1, {})
        elif method_name == 'update_episode':
            method(1, {})
        else:
            method()

        assert session.calls, f"{method_name} issued no HTTP request"
        for call in session.calls:
            assert 'timeout' in call['kwargs'], (
                f"{method_name} called {verb} without a timeout - a stalled "
                "connection would hang the poll loop forever")
            assert call['kwargs']['timeout'] == main.REQUEST_TIMEOUT

    def test_timeout_value_is_finite_and_positive(self):
        """A None/0/inf timeout would defeat the guard."""
        assert isinstance(main.REQUEST_TIMEOUT, (int, float))
        assert main.REQUEST_TIMEOUT > 0

    def test_create_tag_passes_a_timeout(self):
        """create_tag is the POST path and needs the same protection."""
        client, session = _make_client(
            {'post': FakeResponse({'id': 9, 'label': 'new'})})
        client.create_tag('new')
        assert session.calls[0]['kwargs']['timeout'] == main.REQUEST_TIMEOUT

    def test_no_call_ever_omits_timeout(self):
        """Exhaustive sweep: exercise every method, assert every call had one."""
        client, session = _make_client({
            'get': FakeResponse([]),
            'post': FakeResponse({'id': 1, 'label': 'l'}),
            'put': FakeResponse({}),
        })
        client.get_shows()
        client.get_tags()
        client.get_episode_files(1)
        client.get_episodes(1)
        client.create_tag('l')
        client.update_show(1, {})
        client.update_episode(1, {})

        assert len(session.calls) == 7
        missing = [call['method'] for call in session.calls
                   if call['kwargs'].get('timeout') != main.REQUEST_TIMEOUT]
        assert not missing, f"calls without the expected timeout: {missing}"

class TestEndpoints:
    """URL construction for each Sonarr API v3 endpoint."""

    @pytest.mark.parametrize('method_name,expected_path', [
        ('get_shows', '/api/v3/series'),
        ('get_tags', '/api/v3/tag'),
    ])
    def test_simple_endpoints(self, method_name, expected_path):
        """GET endpoints use the documented v3 paths."""
        client, session = _make_client({'get': FakeResponse([])})
        getattr(client, method_name)()
        assert session.calls[0]['url'] == f"{BASE_URL}{expected_path}"

    def test_episode_files_uses_series_id(self):
        """Episode files are fetched per series."""
        client, session = _make_client({'get': FakeResponse([])})
        client.get_episode_files(42)
        assert session.calls[0]['url'] == \
            f"{BASE_URL}/api/v3/episodefile?seriesId=42"

    def test_episodes_uses_series_id(self):
        """Episodes are fetched per series."""
        client, session = _make_client({'get': FakeResponse([])})
        client.get_episodes(42)
        assert session.calls[0]['url'] == \
            f"{BASE_URL}/api/v3/episode?seriesId=42"

    def test_update_show_puts_to_series_id(self):
        """Show updates PUT to the series resource."""
        client, session = _make_client({'put': FakeResponse({})})
        client.update_show(7, {'id': 7})
        assert session.calls[0]['method'] == 'put'
        assert session.calls[0]['url'] == f"{BASE_URL}/api/v3/series/7"
        assert session.calls[0]['kwargs']['json'] == {'id': 7}

    def test_update_episode_puts_to_episode_id(self):
        """Episode updates PUT to the episode resource."""
        client, session = _make_client({'put': FakeResponse({})})
        client.update_episode(9, {'id': 9, 'monitored': True})
        assert session.calls[0]['url'] == f"{BASE_URL}/api/v3/episode/9"
        assert session.calls[0]['kwargs']['json']['monitored'] is True

    def test_create_tag_posts_label(self):
        """Tag creation POSTs the label payload Sonarr expects."""
        client, session = _make_client(
            {'post': FakeResponse({'id': 3, 'label': 'motong'})})
        client.create_tag('motong')
        assert session.calls[0]['url'] == f"{BASE_URL}/api/v3/tag"
        assert session.calls[0]['kwargs']['json'] == {'label': 'motong'}

    def test_create_tag_returns_created_tag(self):
        """The created tag payload is returned to the caller."""
        client, _ = _make_client(
            {'post': FakeResponse({'id': 3, 'label': 'motong'})})
        assert client.create_tag('motong') == {'id': 3, 'label': 'motong'}

class TestSuccessPaths:
    """Return values on the happy path."""

    def test_get_shows_returns_payload(self):
        """The parsed JSON body is returned directly."""
        shows = [{'id': 1, 'title': 'A'}]
        client, _ = _make_client({'get': FakeResponse(shows)})
        assert client.get_shows() == shows

    def test_get_tags_returns_payload(self):
        """Tags come back as a list of {id, label} dicts."""
        tags = [{'id': 1, 'label': 'x'}]
        client, _ = _make_client({'get': FakeResponse(tags)})
        assert client.get_tags() == tags

    def test_raise_for_status_is_called(self):
        """HTTP status errors must be surfaced, not silently ignored."""
        client, _ = _make_client({'get': FakeResponse([], status_code=500)})
        with pytest.raises(HTTPError):
            client.get_shows()

    def test_update_show_returns_true_on_success(self):
        """A successful update reports success to the caller."""
        client, _ = _make_client({'put': FakeResponse({})})
        assert client.update_show(1, {}) is True

    def test_update_episode_returns_true_on_success(self):
        """A successful episode update reports success."""
        client, _ = _make_client({'put': FakeResponse({})})
        assert client.update_episode(1, {}) is True

class TestErrorPaths:
    """Network failures must degrade gracefully, not crash the poll loop."""

    @pytest.mark.parametrize('method_name,args', [
        ('get_shows', ()),
        ('get_tags', ()),
        ('get_episode_files', (1,)),
        ('get_episodes', (1,)),
    ])
    def test_get_failures_propagate_after_logging(self, method_name, args):
        """Read failures log then re-raise, for the caller to handle.

        The client deliberately does not swallow these: ``run_once`` is the level
        that decides to retry, and hiding the failure here would mask a dead
        Sonarr while the loop reported healthy runs. The re-raise is what lets
        ``main()``'s ``except RequestException`` schedule the 5-minute retry.
        """
        client, _ = _make_client({'get': FakeResponse([], status_code=503)})
        with pytest.raises(RequestException):
            getattr(client, method_name)(*args)

    def test_request_exception_propagates_for_reads(self, caplog):
        """Connection errors on reads are logged, then propagate."""
        class ExplodingSession(FakeSession):
            """Session that always fails to connect."""

            def get(self, url, **kwargs):
                raise RequestsConnectionError("connection refused")

        client = main.SonarrAPI(BASE_URL, API_KEY, session=ExplodingSession())
        with pytest.raises(RequestException):
            client.get_shows()
        assert 'Failed to fetch shows' in caplog.text

    def test_read_failure_is_logged_with_context(self, caplog):
        """The log line names what could not be fetched."""
        client, _ = _make_client({'get': FakeResponse([], status_code=503)})
        with pytest.raises(RequestException):
            client.get_episode_files(42)
        assert 'episode files' in caplog.text

    def test_update_show_failure_returns_false(self):
        """A failed update returns False so it is not counted as updated."""
        client, _ = _make_client({'put': FakeResponse({}, status_code=500)})
        assert client.update_show(1, {}) is False

    def test_update_episode_failure_returns_false(self):
        """A failed episode update returns False."""
        client, _ = _make_client({'put': FakeResponse({}, status_code=500)})
        assert client.update_episode(1, {}) is False

    def test_update_show_request_exception_returns_false(self):
        """Connection errors on writes are contained and reported as failure."""
        class ExplodingSession(FakeSession):
            """Session that always fails on writes."""

            def put(self, url, **kwargs):
                raise RequestsConnectionError("connection refused")

        client = main.SonarrAPI(BASE_URL, API_KEY, session=ExplodingSession())
        assert client.update_show(1, {}) is False

    def test_request_exception_is_a_request_exception(self):
        """Sanity check: the caught type really is RequestException."""
        assert issubclass(RequestsConnectionError, RequestException)

    def test_create_tag_failure_is_logged_and_propagates(self, caplog):
        """A failed tag creation is logged and re-raised for the loop to retry."""
        client, _ = _make_client({'post': FakeResponse({}, status_code=500)})
        with pytest.raises(RequestException):
            client.create_tag('motong')
        assert "Failed to create tag 'motong'" in caplog.text

class TestAuthenticationRejection:
    """401/403 become AuthenticationError so the loop can stop retrying.

    Retrying a rejected key can never succeed. Before this the generic
    HTTPError was swallowed by the retry branch and the process logged one line
    every five minutes forever - the exact silent failure seen in production,
    where three stale processes with an empty key emitted 401s for a day.
    """

    @pytest.mark.parametrize('status_code', [401, 403])
    @pytest.mark.parametrize('method_name,args', [
        ('get_shows', ()),
        ('get_tags', ()),
        ('get_episode_files', (1,)),
    ])
    def test_get_rejections_raise_authentication_error(self, status_code,
                                                       method_name, args):
        """Every GET surfaces an auth rejection as AuthenticationError."""
        client, _ = _make_client(
            {'get': FakeResponse({}, status_code=status_code)})
        with pytest.raises(main.AuthenticationError):
            getattr(client, method_name)(*args)

    @pytest.mark.parametrize('status_code', [401, 403])
    def test_update_show_rejection_is_logged_and_returns_false(self, status_code,
                                                              caplog):
        """A PUT rejection does not raise: update_show reports failure.

        The existing contract is a boolean, and _update_show_tags relies on it.
        AuthenticationError still subclasses RequestException, so it is caught
        by the same handler.
        """
        client, _ = _make_client(
            {'put': FakeResponse({}, status_code=status_code)})
        assert client.update_show(1, {}) is False
        assert 'Failed to update show 1' in caplog.text

    def test_create_tag_auth_rejection_propagates(self):
        """A tag-creation rejection reaches the loop as AuthenticationError."""
        client, _ = _make_client(
            {'post': FakeResponse({}, status_code=401)})
        with pytest.raises(main.AuthenticationError):
            client.create_tag('motong')

    def test_authentication_error_is_not_raised_for_other_4xx(self):
        """A plain 404 stays a generic HTTPError, not an auth failure."""
        client, _ = _make_client({'get': FakeResponse([], status_code=404)})
        with pytest.raises(HTTPError) as excinfo:
            client.get_shows()
        assert not isinstance(excinfo.value, main.AuthenticationError)
