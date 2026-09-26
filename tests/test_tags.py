"""Tests for the entry point: argument parsing, log setup and fail-fast main()."""

import logging
import sys

import pytest

import main

REQUIRED = {
    'SONARR_URL': 'http://sonarr:8989',
    'SONARR_API_KEY': 'secret',
}

class _RecordCollector:
    """Capture log records across setup_logging()'s handler reset.

    ``setup_logging()`` assigns ``logging.root.handlers = []``, which deletes
    pytest's caplog handler and leaves ``caplog.records`` empty - the assertions
    would fail even though the messages were emitted. ``logging.Logger.handle``
    is the one stable interception point, since it runs for any handler set.
    """

    def __init__(self):
        self.records = []

    def __enter__(self):
        self._original = logging.Logger.handle
        collector = self

        def handle(logger_self, record):
            collector.records.append(record)
            return self._original(logger_self, record)

        logging.Logger.handle = handle
        return self

    def __exit__(self, *exc_info):
        logging.Logger.handle = self._original
        return False

    @property
    def messages(self):
        """Rendered messages of every captured record."""
        return [record.getMessage() for record in self.records]


class TestParseArgs:
    """Command line surface."""

    def test_no_arguments_defaults_to_empty(self, monkeypatch):
        """Bare invocation is a normal (non-test) run."""
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        args = main.parse_args()
        assert args.test is False
        assert args.version is False

    def test_test_flag(self, monkeypatch):
        """--test enables test mode."""
        monkeypatch.setattr(sys, 'argv', ['main.py', '--test'])
        assert main.parse_args().test is True

    def test_version_flag(self, monkeypatch):
        """--version is recognised."""
        monkeypatch.setattr(sys, 'argv', ['main.py', '--version'])
        assert main.parse_args().version is True

    def test_unknown_flag_exits(self, monkeypatch):
        """An unrecognised flag is a usage error."""
        monkeypatch.setattr(sys, 'argv', ['main.py', '--nonsense'])
        with pytest.raises(SystemExit):
            main.parse_args()

class TestVersion:
    """The reported version must match the released tag."""

    def test_version_is_semver(self):
        """VERSION looks like a semantic version."""
        parts = main.VERSION.split('.')
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)

    def test_version_flag_prints_and_exits_zero(self, monkeypatch, capsys):
        """--version short-circuits before any configuration is read."""
        monkeypatch.setattr(sys, 'argv', ['main.py', '--version'])
        # No SONARR_URL is set: if --version read config it would exit 1 instead.
        monkeypatch.delenv('SONARR_URL', raising=False)
        monkeypatch.delenv('SONARR_API_KEY', raising=False)
        with pytest.raises(SystemExit) as excinfo:
            main.main()

        assert excinfo.value.code == 0
        assert f"Sonarr Tag Updater v{main.VERSION}" in capsys.readouterr().out

class TestSetupLogging:
    """setup_logging configures the root logger and announces the level."""

    def test_log_level_is_applied(self):
        """The requested level becomes the root logger level."""
        try:
            main.setup_logging('DEBUG')
            assert logging.getLogger().level == logging.DEBUG
        finally:
            main.setup_logging('INFO')

    def test_initialisation_is_logged(self):
        """The applied level is reported so operators can confirm it took."""
        with _RecordCollector() as collector:
            main.setup_logging('INFO')
        assert any('Logging initialized at level: INFO' in message
                   for message in collector.messages)

    def test_info_line_suppressed_at_warning_level(self):
        """The INFO confirmation is not emitted when the level hides it."""
        with _RecordCollector() as collector:
            main.setup_logging('WARNING')
        try:
            assert not any('Logging initialized at level' in message
                           for message in collector.messages)
        finally:
            main.setup_logging('INFO')

    def test_existing_handlers_are_replaced(self):
        """Repeated calls do not accumulate duplicate handlers."""
        main.setup_logging('INFO')
        first = len(logging.getLogger().handlers)
        main.setup_logging('INFO')
        assert len(logging.getLogger().handlers) == first

class TestMainFailFast:
    """main() must exit non-zero with a clear message on bad configuration.

    Before this, an unset SONARR_URL produced a raw KeyError traceback and a
    non-zero exit, but with no indication of which variable was missing.
    """

    def _run_main(self, monkeypatch, env):
        """Invoke main() with a controlled environment."""
        for name in ('SONARR_URL', 'SONARR_API_KEY', 'LOG_LEVEL',
                     'INTERVAL_MINUTES'):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        main.main()

    def test_missing_url_exits_one(self, monkeypatch, caplog):
        """A missing SONARR_URL exits 1 instead of raising KeyError."""
        with pytest.raises(SystemExit) as excinfo:
            self._run_main(monkeypatch, {'SONARR_API_KEY': 'secret'})
        assert excinfo.value.code == 1

    def test_missing_url_logs_configuration_error(self, monkeypatch, caplog):
        """The failure is reported as a configuration error naming the variable."""
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit):
                self._run_main(monkeypatch, {'SONARR_API_KEY': 'secret'})
        assert 'Configuration error' in caplog.text
        assert 'SONARR_URL' in caplog.text

    def test_empty_url_exits_one(self, monkeypatch):
        """An empty SONARR_URL is caught before any API call."""
        with pytest.raises(SystemExit) as excinfo:
            self._run_main(monkeypatch, {'SONARR_URL': '', 'SONARR_API_KEY': 'k'})
        assert excinfo.value.code == 1

    def test_invalid_interval_exits_one(self, monkeypatch, caplog):
        """A zero INTERVAL_MINUTES is rejected before the loop starts.

        This is the anti-busy-loop guard: a zero interval would make the poll
        loop spin at full CPU instead of exiting.
        """
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit) as excinfo:
                self._run_main(monkeypatch, {**REQUIRED, 'INTERVAL_MINUTES': '0'})
        assert excinfo.value.code == 1
        assert 'INTERVAL_MINUTES' in caplog.text

    def test_invalid_log_level_exits_one(self, monkeypatch):
        """A bad LOG_LEVEL fails fast rather than inside logging.basicConfig."""
        with pytest.raises(SystemExit) as excinfo:
            self._run_main(monkeypatch, {**REQUIRED, 'LOG_LEVEL': 'LOUD'})
        assert excinfo.value.code == 1

    def test_no_api_client_created_on_bad_config(self, monkeypatch):
        """Configuration is validated before the HTTP client is constructed."""
        created = []
        original = main.SonarrAPI

        class Spy(original):
            """Record construction attempts."""

            def __init__(self, *args, **kwargs):
                created.append(args)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(main, 'SonarrAPI', Spy)
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, {})
        assert created == []

class TestMainPollLoop:
    """The poll loop's timing and retry behaviour."""

    def _prepare(self, monkeypatch):
        """Set a valid environment and neutralise tag creation."""
        for name, value in REQUIRED.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv('INTERVAL_MINUTES', '20')
        monkeypatch.setenv('LOG_LEVEL', 'INFO')
        monkeypatch.setattr(sys, 'argv', ['main.py'])

    def test_sleeps_interval_then_loop_continues(self, monkeypatch):
        """After a successful pass the loop sleeps interval*60 and runs again."""
        self._prepare(monkeypatch)
        sleeps = []
        runs = []

        def record_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise KeyboardInterrupt

        def fake_run_once(api, config, test_mode=False):
            runs.append(test_mode)
            return 0

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', fake_run_once)
        with pytest.raises(KeyboardInterrupt):
            main.main()

        assert sleeps == [20 * 60, 20 * 60]
        assert len(runs) == 2

    def test_retries_after_five_minutes_on_failure(self, monkeypatch):
        """A failed pass logs the error and retries after 5 minutes."""
        self._prepare(monkeypatch)
        sleeps = []

        def failing_run_once(api, config, test_mode=False):
            raise main.RequestException('boom')

        def record_sleep(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', failing_run_once)
        with pytest.raises(KeyboardInterrupt):
            main.main()

        assert sleeps == [300]

    def test_value_error_from_run_once_is_retried(self, monkeypatch):
        """ValueError is caught by the loop too, not just RequestException."""
        self._prepare(monkeypatch)
        sleeps = []

        def bad_run_once(api, config, test_mode=False):
            raise ValueError('unexpected')

        def record_sleep(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', bad_run_once)
        with pytest.raises(KeyboardInterrupt):
            main.main()

        assert sleeps == [300]

    def test_failure_logged_and_recovery_continues(self, monkeypatch, caplog):
        """After a failure the loop recovers on the next successful pass."""
        self._prepare(monkeypatch)
        outcomes = [main.RequestException('boom'), None]
        sleeps = []

        def flaky_run_once(api, config, test_mode=False):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome

        def record_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', flaky_run_once)
        with _RecordCollector() as collector:
            with pytest.raises(KeyboardInterrupt):
                main.main()

        # A failure retry (300s), then a normal interval sleep.
        assert sleeps == [300, 20 * 60]
        assert any('Script failed: boom' in message
                   for message in collector.messages)
        assert any('Retrying in 5 minutes' in message
                   for message in collector.messages)

    def test_authentication_error_is_fatal_and_not_retried(self, monkeypatch):
        """A rejected API key exits 1 instead of retrying forever.

        Retrying cannot repair a wrong key. Before this, the loop logged one line
        every five minutes indefinitely while tagging nothing - three processes
        with an empty key did exactly that for a day, and the only symptom was
        unrelated 401 noise in the Radarr log. The container must die so its
        restart policy shows the failure.
        """
        self._prepare(monkeypatch)
        sleeps = []

        def reject(api, config, test_mode=False):
            raise main.AuthenticationError('Sonarr rejected the API key (HTTP 401)')

        def record_sleep(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', reject)
        with _RecordCollector() as collector:
            with pytest.raises(SystemExit) as excinfo:
                main.main()

        assert excinfo.value.code == 1
        assert sleeps == [], 'a rejected key must not be retried after 5 minutes'
        assert any('Authentication failed' in message
                   for message in collector.messages)

    def test_authentication_error_is_a_request_exception(self):
        """AuthenticationError stays catchable as a RequestException.

        Every call site already catches RequestException, so subclassing it keeps
        all existing failure handling intact while main() singles this case out.
        """
        assert issubclass(main.AuthenticationError, main.RequestException)

    def test_test_mode_flag_forwarded(self, monkeypatch):
        """--test is passed through to run_once."""
        self._prepare(monkeypatch)
        monkeypatch.setattr(sys, 'argv', ['main.py', '--test'])
        seen = []

        def fake_run_once(api, config, test_mode=False):
            seen.append(test_mode)
            raise KeyboardInterrupt

        monkeypatch.setattr(main, 'run_once', fake_run_once)
        with pytest.raises(KeyboardInterrupt):
            main.main()

        assert seen == [True]

    def test_interval_comes_from_config(self, monkeypatch):
        """The validated config interval drives the sleep, not a fresh getenv."""
        self._prepare(monkeypatch)
        monkeypatch.setenv('INTERVAL_MINUTES', '7')
        sleeps = []

        def record_sleep(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(main.time, 'sleep', record_sleep)
        monkeypatch.setattr(main, 'run_once', lambda *a, **k: None)
        with pytest.raises(KeyboardInterrupt):
            main.main()

        assert sleeps == [7 * 60]

class TestLogSequenceParity:
    """The log lines main() emits must survive the run_once extraction.

    run_once was carved out of main() during hardening. The specials summary was
    deliberately kept inside it so the observable log sequence is unchanged; this
    test pins the messages rather than leaving it to inspection.
    """

    def _run_main_with_fakes(self, monkeypatch):
        """Run main() once with a stub API, breaking out via KeyboardInterrupt."""
        for name, value in REQUIRED.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv('LOG_LEVEL', 'INFO')

        from conftest import FakeSonarrAPI
        api = FakeSonarrAPI(
            shows=[], tags=[{'id': i, 'label': label}
                            for i, label in enumerate(main.REQUIRED_TAGS, 1)])
        monkeypatch.setattr(main, 'SonarrAPI', lambda *a, **k: api)
        monkeypatch.setattr(main.time, 'sleep', _break_out_of_loop)

        with _RecordCollector() as collector:
            with pytest.raises(KeyboardInterrupt):
                main.main()
        return collector.messages

    def test_startup_and_completion_lines(self, monkeypatch):
        """Startup, completion and next-run lines are all emitted."""
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        monkeypatch.setenv('INTERVAL_MINUTES', '20')
        messages = self._run_main_with_fakes(monkeypatch)

        assert any('Starting Sonarr Tag Updater v' in m for m in messages)
        assert any('Processing complete.' in m for m in messages)
        assert any('Next run in 20 minutes' in m for m in messages)

    def test_test_mode_line_emitted(self, monkeypatch):
        """The TEST MODE notice still appears before the pass."""
        monkeypatch.setattr(sys, 'argv', ['main.py', '--test'])
        messages = self._run_main_with_fakes(monkeypatch)
        assert any('TEST MODE: Processing first 5 shows only' in m
                   for m in messages)

    def test_specials_line_present_when_enabled(self, monkeypatch):
        """The specials summary is logged when the feature is on."""
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        monkeypatch.setenv('MONITOR_EXISTING_SPECIALS', 'true')
        messages = self._run_main_with_fakes(monkeypatch)
        assert any('special episode(s) to monitored' in m for m in messages)

    def test_specials_line_absent_when_disabled(self, monkeypatch):
        """The specials summary is suppressed when the feature is off."""
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        monkeypatch.delenv('MONITOR_EXISTING_SPECIALS', raising=False)
        messages = self._run_main_with_fakes(monkeypatch)
        assert any('Processing complete.' in m for m in messages)
        assert not any('special episode(s) to monitored' in m for m in messages)

    def test_logging_initialised_line_emitted(self, monkeypatch):
        """setup_logging reports the level it applied."""
        monkeypatch.setattr(sys, 'argv', ['main.py'])
        messages = self._run_main_with_fakes(monkeypatch)
        assert any('Logging initialized at level: INFO' in m for m in messages)

def _break_out_of_loop(*_args, **_kwargs):
    """Break out of the otherwise infinite poll loop."""
    raise KeyboardInterrupt
