"""Shared pytest fixtures for the sonarr-tagger test suite.

The suite is deliberately dependency-free beyond pytest: ``FakeSession`` mimics
the small slice of the ``requests.Session`` surface that ``SonarrAPI`` uses, and
``FakeSonarrAPI`` records call counts so tests can assert that the client does
not make redundant network requests.
"""

import faulthandler
import os

import pytest
from requests.exceptions import HTTPError, RequestException
from requests.structures import CaseInsensitiveDict

# The application module lives in a hyphenated directory, so rely on the root
# conftest.py having already placed it on sys.path.
import main  # noqa: E402  pylint: disable=wrong-import-position

# Any test that reaches a real sleep is a bug (see the ``forbid_real_sleep``
# fixture), so a hang means the guard is missing - not that we should wait. Dump
# every thread's traceback after a few seconds and let the runner kill the file.
_HANG_TIMEOUT_SECONDS = float(os.getenv('PYTEST_HANG_TIMEOUT', '10'))
faulthandler.dump_traceback_later(_HANG_TIMEOUT_SECONDS, exit=True)

DEFAULT_TAG_MAP = {
    'negative-score': 1,
    'positive-score': 2,
    'no-score': 3,
    'motong': 4,
    '4k': 5,
    'mixed-release-groups': 6,
}

class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = str(self._payload)

    def raise_for_status(self):
        """Raise like requests does for 4xx/5xx responses."""
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} error")

    def json(self):
        """Return the canned JSON payload."""
        return self._payload

class FakeSession:
    """Records every HTTP call and replays canned responses.

    ``responses`` maps an HTTP method name to either a single response or a list
    of responses consumed in order (the last one repeats).
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        # Mirrors requests.Session.headers, which is a CaseInsensitiveDict so that
        # header lookups are case-insensitive in production as well as in tests.
        self.headers = CaseInsensitiveDict()
        self.calls = []

    def _next_response(self, method):
        """Return the next canned response for an HTTP method."""
        queued = self.responses.get(method)
        if queued is None:
            raise AssertionError(f"no canned response for {method.upper()}")
        if isinstance(queued, list):
            if len(queued) > 1:
                return queued.pop(0)
            return queued[0]
        return queued

    def _record(self, method, url, **kwargs):
        """Record a call and return its canned response."""
        self.calls.append({'method': method, 'url': url, 'kwargs': kwargs})
        return self._next_response(method)

    def get(self, url, **kwargs):
        """Record a GET and replay its canned response."""
        return self._record('get', url, **kwargs)

    def post(self, url, **kwargs):
        """Record a POST and replay its canned response."""
        return self._record('post', url, **kwargs)

    def put(self, url, **kwargs):
        """Record a PUT and replay its canned response."""
        return self._record('put', url, **kwargs)

class FakeSonarrAPI:
    """In-memory stand-in for ``SonarrAPI`` that counts every call.

    ``fail_on`` maps a method name to the exception it should raise, so error
    paths can be exercised without a network. ``update_result`` controls what
    ``update_show`` returns, letting tests simulate partial failures.
    """

    def __init__(self, shows=None, tags=None, episode_files=None,
                 episodes=None, fail_on=None, update_result=True):
        self.shows = shows if shows is not None else []
        self.tags = tags if tags is not None else []
        self.episode_files = episode_files or {}
        self.episodes = episodes or {}
        self.fail_on = fail_on or {}
        self.update_result = update_result
        self.updates = []
        self.episode_updates = []
        self.created_tags = []
        self.calls = {
            'get_shows': 0,
            'get_show': 0,
            'get_tags': 0,
            'create_tag': 0,
            'get_episode_files': 0,
            'get_episodes': 0,
            'update_show': 0,
            'update_episode': 0,
        }
        self._created_id = max(
            (tag['id'] for tag in self.tags), default=0)

    def _maybe_fail(self, method):
        """Raise the configured exception for a method, if any."""
        if method in self.fail_on:
            raise self.fail_on[method]

    def get_shows(self):
        """Return the configured show list."""
        self.calls['get_shows'] += 1
        self._maybe_fail('get_shows')
        return self.shows

    def get_show(self, series_id):
        """Return a *copy* of one configured show.

        The copy matters: production code re-reads the show immediately before
        PUT so that it writes fresh server state rather than the stale snapshot
        captured at the start of the pass. Returning the same object here would
        let a stale-snapshot bug pass unnoticed.
        """
        self.calls['get_show'] += 1
        self._maybe_fail('get_show')
        for show in self.shows:
            if show['id'] == series_id:
                return dict(show)
        raise AssertionError(f"unexpected seriesId {series_id}")

    def get_tags(self):
        """Return the configured tag list."""
        self.calls['get_tags'] += 1
        self._maybe_fail('get_tags')
        return self.tags

    def create_tag(self, label):
        """Create and remember a tag, mirroring the Sonarr response shape."""
        self.calls['create_tag'] += 1
        self._maybe_fail('create_tag')
        self._created_id += 1
        new_tag = {'id': self._created_id, 'label': label}
        self.tags.append(new_tag)
        self.created_tags.append(label)
        return new_tag

    def get_episode_files(self, series_id):
        """Return the configured episode file list for a series."""
        self.calls['get_episode_files'] += 1
        self._maybe_fail('get_episode_files')
        if series_id not in self.episode_files:
            raise AssertionError(f"unexpected seriesId {series_id}")
        return self.episode_files[series_id]

    def get_episodes(self, series_id, season_number=None):
        """Return the configured episode list for a series.

        ``season_number`` mirrors the real client's filter. The fake applies it
        (returning only matching episodes) so a test can prove the caller passes
        season 0 rather than merely that the parameter exists.
        """
        self.calls['get_episodes'] += 1
        self._maybe_fail('get_episodes')
        if series_id not in self.episodes:
            raise AssertionError(f"unexpected seriesId {series_id}")
        episodes = self.episodes[series_id]
        if season_number is None:
            return episodes
        return [e for e in episodes if e.get('seasonNumber') == season_number]

    def update_show(self, series_id, series_data):
        """Record an update and return the configured result."""
        self.calls['update_show'] += 1
        self.updates.append((series_id, series_data))
        self._maybe_fail('update_show')
        return self.update_result

    def update_episode(self, episode_id, episode_data):
        """Record an episode update and return the configured result."""
        self.calls['update_episode'] += 1
        self.episode_updates.append((episode_id, episode_data))
        self._maybe_fail('update_episode')
        return self.update_result

def make_show(show_id=1, title='Test Show', tags=None, **extra):
    """Build a show payload resembling Sonarr's /api/v3/series response."""
    show = {
        'id': show_id,
        'title': title,
        'tags': list(tags) if tags else [],
    }
    show.update(extra)
    return show

def make_episode_file(score=0, release_group='GRP', resolution=1080,
                      season_number=1):
    """Build an episode file payload resembling /api/v3/episodefile."""
    return {
        'id': 10,
        'customFormatScore': score,
        'releaseGroup': release_group,
        'seasonNumber': season_number,
        'quality': {
            'quality': {
                'resolution': resolution,
            }
        },
    }

def make_episode(episode_id=1, season_number=0, episode_number=1,
                 has_file=True, monitored=False, **extra):
    """Build an episode payload resembling Sonarr's /api/v3/episode response."""
    episode = {
        'id': episode_id,
        'seasonNumber': season_number,
        'episodeNumber': episode_number,
        'hasFile': has_file,
        'monitored': monitored,
    }
    episode.update(extra)
    return episode

@pytest.fixture
def full_tag_map():
    """A label->id map shaped like ``ensure_required_tags()`` really returns.

    ``ensure_required_tags()`` maps *every* tag that exists in Sonarr, not only
    the managed ones, so a realistic map must contain unmanaged entries. Do not
    "simplify" this back to DEFAULT_TAG_MAP: tests built on a managed-only map
    cannot detect unmanaged tags being stripped (that gap is exactly how the
    tag-wiping bug shipped).
    """
    return {**DEFAULT_TAG_MAP, 'requested': 98, 'potential-delete': 99,
            'no-new-seasons': 97, 'custom-mkv': 96}

@pytest.fixture
def tag_map():
    """Return a default label->id mapping for the managed tags."""
    return dict(DEFAULT_TAG_MAP)

@pytest.fixture
def base_config():
    """Return a config dict with every optional feature disabled."""
    return {
        'sonarr_url': 'http://sonarr:8989',
        'sonarr_api_key': 'test-key',
        'log_level': 'INFO',
        'score_threshold': 100,
        'tag_motong_enabled': False,
        'tag_4k_enabled': False,
        'tag_mixed_release_groups_enabled': False,
        'monitor_existing_specials_enabled': False,
    }

@pytest.fixture
def env_guard(monkeypatch):
    """Provide a helper that sets/removes environment variables cleanly."""

    def _apply(values):
        for key, value in values.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

    return _apply

@pytest.fixture(autouse=True)
def forbid_real_sleep(monkeypatch):
    """Fail fast if production code tries to sleep for real.

    ``main.main()`` polls forever in a ``while True`` loop, so a config-validation
    regression (for example a non-positive ``INTERVAL_MINUTES`` slipping past the
    bounds check) turns into an unbounded ``time.sleep(0)`` busy loop: the test
    never returns, the mutation harness never advances, and CI burns its whole
    job timeout with no useful diagnostic.

    Replacing ``main.time.sleep`` makes that failure mode explicit and immediate.
    Tests that exercise the loop patch ``main.time.sleep`` themselves; monkeypatch
    is applied inside the test body, so their recorder still takes precedence over
    this guard.
    """
    def _unexpected_sleep(seconds, *args, **kwargs):
        raise AssertionError(
            f"main.time.sleep({seconds!r}) was called for real - this test would "
            "hang (the poll loop is unbounded). Expected the delay to be patched by "
            "the test, or prevented by config validation in get_interval_minutes(). "
            f"faulthandler kills the run after {_HANG_TIMEOUT_SECONDS}s.")

    monkeypatch.setattr(main.time, 'sleep', _unexpected_sleep, raising=True)
