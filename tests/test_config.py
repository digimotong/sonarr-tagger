"""Tests for environment configuration loading and validation."""

import io
import logging

import pytest

import main

REQUIRED = {
    'SONARR_URL': 'http://sonarr:8989',
    'SONARR_API_KEY': 'secret',
}

def _clear_optional(env_guard):
    """Remove every optional variable so tests see only defaults."""
    env_guard({
        'INTERVAL_MINUTES': None,
        'LOG_LEVEL': None,
        'SCORE_THRESHOLD': None,
        'TAG_MOTONG': None,
        'TAG_4K': None,
        'TAG_MIXED_RELEASE_GROUPS': None,
        'MONITOR_EXISTING_SPECIALS': None,
    })

class TestRequiredEnvVars:
    """Required variables must fail fast and name the missing ones."""

    def test_missing_url_raises_value_error_not_keyerror(self, env_guard):
        """A missing SONARR_URL raised KeyError before; it must be a ValueError."""
        env_guard({'SONARR_URL': None, 'SONARR_API_KEY': 'secret'})
        with pytest.raises(ValueError) as excinfo:
            main.get_config_from_env()
        assert 'SONARR_URL' in str(excinfo.value)
        assert 'must be set' in str(excinfo.value)

    def test_missing_api_key_raises_value_error(self, env_guard):
        """A missing SONARR_API_KEY must raise a ValueError naming it."""
        env_guard({'SONARR_URL': 'http://sonarr:8989', 'SONARR_API_KEY': None})
        with pytest.raises(ValueError) as excinfo:
            main.get_config_from_env()
        assert 'SONARR_API_KEY' in str(excinfo.value)

    def test_both_missing_reported_together(self, env_guard):
        """Both missing variables are named in one message, not one at a time."""
        env_guard({'SONARR_URL': None, 'SONARR_API_KEY': None})
        with pytest.raises(ValueError) as excinfo:
            main.get_config_from_env()
        message = str(excinfo.value)
        assert 'SONARR_URL must be set' in message
        assert 'SONARR_API_KEY must be set' in message

    @pytest.mark.parametrize('blank', ['', '   '])
    def test_empty_value_is_treated_as_missing(self, env_guard, blank):
        """Whitespace-only values are as useless as unset ones."""
        env_guard({'SONARR_URL': blank, 'SONARR_API_KEY': 'secret'})
        with pytest.raises(ValueError, match='SONARR_URL'):
            main.get_config_from_env()

class TestIntervalMinutes:
    """INTERVAL_MINUTES governs time.sleep in the poll loop, so validate it."""

    def test_default_is_twenty(self, env_guard):
        """The documented default (README: 20) is preserved."""
        _clear_optional(env_guard)
        env_guard(REQUIRED)
        assert main.get_config_from_env()['interval_minutes'] == 20

    @pytest.mark.parametrize('value,expected', [
        ('1', 1),
        ('20', 20),
        ('  45  ', 45),
        ('525600', 525_600),
    ])
    def test_valid_values_are_accepted(self, env_guard, value, expected):
        """In-range integers parse, surrounding whitespace included."""
        env_guard({'INTERVAL_MINUTES': value})
        assert main.get_interval_minutes() == expected

    @pytest.mark.parametrize('value', ['0', '-1', '-100'])
    def test_non_positive_is_rejected(self, env_guard, value):
        """A non-positive interval makes time.sleep() return immediately.

        That turns the poll loop into an unbounded busy loop that hammers the
        Sonarr API and never yields - exactly the hang the test suite guards
        against - so it must be rejected at load time.
        """
        env_guard({'INTERVAL_MINUTES': value})
        with pytest.raises(ValueError) as excinfo:
            main.get_interval_minutes()
        assert 'INTERVAL_MINUTES' in str(excinfo.value)
        assert 'between 1 and 525600' in str(excinfo.value)

    @pytest.mark.parametrize('value', ['525601', '99999999'])
    def test_above_maximum_is_rejected(self, env_guard, value):
        """An absurd interval silently stops updates ever running again."""
        env_guard({'INTERVAL_MINUTES': value})
        with pytest.raises(ValueError, match='INTERVAL_MINUTES'):
            main.get_interval_minutes()

    @pytest.mark.parametrize('value', ['abc', '1.5', '', 'twenty'])
    def test_non_integer_is_rejected(self, env_guard, value):
        """Non-integers raise ValueError naming the variable, not int()'s message."""
        env_guard({'INTERVAL_MINUTES': value})
        with pytest.raises(ValueError, match='INTERVAL_MINUTES'):
            main.get_interval_minutes()

class TestLogLevel:
    """LOG_LEVEL must be validated before it reaches logging.basicConfig."""

    @pytest.mark.parametrize('value', ['DEBUG', 'INFO', 'WARNING', 'ERROR',
                                       'CRITICAL'])
    def test_valid_levels_accepted(self, env_guard, value):
        """Every level accepted by logging is accepted here."""
        env_guard({'LOG_LEVEL': value})
        assert main.get_log_level() == value

    @pytest.mark.parametrize('value,expected', [
        ('debug', 'DEBUG'),
        ('Info', 'INFO'),
        ('  warning  ', 'WARNING'),
    ])
    def test_level_is_normalised(self, env_guard, value, expected):
        """Case and whitespace are normalised rather than rejected."""
        env_guard({'LOG_LEVEL': value})
        assert main.get_log_level() == expected

    def test_default_is_info(self, env_guard):
        """The default matches the README."""
        env_guard({'LOG_LEVEL': None, 'SONARR_URL': 'http://sonarr:8989',
                   'SONARR_API_KEY': 'key'})
        assert main.get_log_level() == 'INFO'

    @pytest.mark.parametrize('value', ['VERBOSE', 'TRACE', 'critical!', '123'])
    def test_invalid_level_rejected_with_guidance(self, env_guard, value):
        """The error names the bad value and lists the accepted ones."""
        env_guard({'LOG_LEVEL': value})
        with pytest.raises(ValueError) as excinfo:
            main.get_log_level()
        message = str(excinfo.value)
        assert value in message
        for level in main.VALID_LOG_LEVELS:
            assert level in message

    def test_critical_is_documented_as_valid(self):
        """CRITICAL is accepted by the helper; docs must not omit it."""
        assert 'CRITICAL' in main.VALID_LOG_LEVELS

class TestOptionFlags:
    """Boolean flags and SCORE_THRESHOLD parsing."""

    @pytest.mark.parametrize('value,expected', [
        ('true', True), ('TRUE', True), ('True', True),
        ('false', False), ('FALSE', False), ('yes', False), ('1', False),
        ('', False),
    ])
    def test_boolean_flag_parsing(self, env_guard, value, expected):
        """Only a case-insensitive 'true' enables a flag."""
        _clear_optional(env_guard)
        env_guard({**REQUIRED, 'TAG_MOTONG': value})
        assert main.get_config_from_env()['tag_motong_enabled'] is expected

    @pytest.mark.parametrize('flag,key', [
        ('TAG_MOTONG', 'tag_motong_enabled'),
        ('TAG_4K', 'tag_4k_enabled'),
        ('TAG_MIXED_RELEASE_GROUPS', 'tag_mixed_release_groups_enabled'),
        ('MONITOR_EXISTING_SPECIALS', 'monitor_existing_specials_enabled'),
    ])
    def test_each_flag_enables_its_config_key(self, env_guard, flag, key):
        """Each environment flag maps onto its own config key."""
        _clear_optional(env_guard)
        env_guard({**REQUIRED, flag: 'true'})
        assert main.get_config_from_env()[key] is True

    @pytest.mark.parametrize('flag', [
        'TAG_MOTONG', 'TAG_4K', 'TAG_MIXED_RELEASE_GROUPS',
        'MONITOR_EXISTING_SPECIALS',
    ])
    def test_flags_default_to_disabled(self, env_guard, flag):
        """Optional features stay off unless explicitly enabled."""
        _clear_optional(env_guard)
        env_guard(REQUIRED)
        config = main.get_config_from_env()
        assert all(value is False for key, value in config.items()
                   if key.endswith('_enabled'))

    def test_score_threshold_default_is_100(self, env_guard):
        """SCORE_THRESHOLD defaults to 100 as documented."""
        _clear_optional(env_guard)
        env_guard(REQUIRED)
        assert main.get_config_from_env()['score_threshold'] == 100

    @pytest.mark.parametrize('value,expected', [('0', 0), ('250', 250),
                                                ('-50', -50)])
    def test_score_threshold_parses_integers(self, env_guard, value, expected):
        """Negative and zero thresholds are legitimate values."""
        env_guard({**REQUIRED, 'SCORE_THRESHOLD': value})
        assert main.get_config_from_env()['score_threshold'] == expected

    def test_invalid_score_threshold_names_variable(self, env_guard):
        """A bare ValueError from int() is unhelpful; name the variable."""
        env_guard({**REQUIRED, 'SCORE_THRESHOLD': 'not-a-number'})
        with pytest.raises(ValueError, match='SCORE_THRESHOLD'):
            main.get_config_from_env()

class TestFullConfig:
    """The assembled config dict."""

    def test_all_keys_present(self, env_guard):
        """Every key consumers rely on is present."""
        _clear_optional(env_guard)
        env_guard(REQUIRED)
        config = main.get_config_from_env()
        assert set(config) == {
            'sonarr_url', 'sonarr_api_key', 'log_level', 'score_threshold',
            'interval_minutes', 'tag_motong_enabled', 'tag_4k_enabled',
            'tag_mixed_release_groups_enabled',
            'monitor_existing_specials_enabled',
        }

    def test_values_are_typed(self, env_guard):
        """Types match what the API client and loop expect."""
        _clear_optional(env_guard)
        env_guard(REQUIRED)
        config = main.get_config_from_env()
        assert isinstance(config['sonarr_url'], str)
        assert isinstance(config['sonarr_api_key'], str)
        assert isinstance(config['log_level'], str)
        assert isinstance(config['score_threshold'], int)
        assert isinstance(config['interval_minutes'], int)

    def test_configured_values_round_trip(self, env_guard):
        """Explicitly supplied values survive into the config dict."""
        env_guard({**REQUIRED, 'LOG_LEVEL': 'debug', 'SCORE_THRESHOLD': '42',
                   'INTERVAL_MINUTES': '15', 'TAG_4K': 'true'})
        config = main.get_config_from_env()
        assert config['sonarr_url'] == 'http://sonarr:8989'
        assert config['sonarr_api_key'] == 'secret'
        assert config['log_level'] == 'DEBUG'
        assert config['score_threshold'] == 42
        assert config['interval_minutes'] == 15
        assert config['tag_4k_enabled'] is True
