"""Documentation drift guards.

Documentation is the only interface most users of this container ever read, so the
README is asserted against the code rather than trusted:

* the version mentioned in the README must match ``main.VERSION`` (it said v1.0.0
  while the code shipped 1.0.7);
* every environment variable the code reads must be documented;
* operational behaviour users depend on (fail-fast, interval bounds, exact-match
  tagging) must be described as implemented.

The tests check for the *presence* of facts rather than exact prose, so the
documentation can be reworded freely.
"""

import os
import re

import pytest

import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README_PATH = os.path.join(REPO_ROOT, 'README.md')
ENV_EXAMPLE_PATH = os.path.join(REPO_ROOT, '.env.example')

@pytest.fixture(scope='module')
def readme():
    """Return the README contents."""
    with open(README_PATH, encoding='utf-8') as handle:
        return handle.read()

@pytest.fixture(scope='module')
def env_example():
    """Return the sample environment file contents."""
    with open(ENV_EXAMPLE_PATH, encoding='utf-8') as handle:
        return handle.read()

class TestVersionDocumentation:
    """The README must not claim a version the code does not have."""

    def test_readme_shows_current_version(self, readme):
        """Any vX.Y.Z mentioned in the README is the shipped VERSION."""
        mentioned = set(re.findall(r'v\d+\.\d+\.\d+', readme))
        assert mentioned, "README no longer mentions a version at all"
        assert mentioned == {f"v{main.VERSION}"}

class TestEnvironmentDocumentation:
    """Every environment variable the code reads is documented."""

    # Kept explicit rather than scraped from the source so an accidental rename
    # fails here instead of silently matching a new name.
    DOCUMENTED_VARS = (
        'SONARR_URL',
        'SONARR_API_KEY',
        'LOG_LEVEL',
        'SCORE_THRESHOLD',
        'INTERVAL_MINUTES',
        'TAG_MOTONG',
        'TAG_4K',
        'TAG_MIXED_RELEASE_GROUPS',
        'MONITOR_EXISTING_SPECIALS',
    )

    @pytest.mark.parametrize('name', DOCUMENTED_VARS)
    def test_readme_documents_variable(self, readme, name):
        """Each variable appears in the README."""
        assert name in readme

    @pytest.mark.parametrize('name', DOCUMENTED_VARS)
    def test_env_example_documents_variable(self, env_example, name):
        """The sample .env covers the same set as the README."""
        assert name in env_example

    def test_code_variables_are_all_documented(self, readme, env_example):
        """No variable read by the code is missing from the docs."""
        source = _read_source()
        used = set(re.findall(r"os\.(?:getenv|environ\[)\(?'?([A-Z_]+)'?",
                              source))
        # os.environ['X'] indexes are matched by a second, simpler pattern.
        used |= set(re.findall(r"os\.environ\['([A-Z_]+)'\]", source))
        undocumented = sorted(name for name in used
                              if name not in readme or name not in env_example)
        assert not undocumented, (
            f"environment variables used in code but not documented: "
            f"{undocumented}")

    def test_log_level_levels_documented(self, readme):
        """Every accepted LOG_LEVEL is listed, CRITICAL included."""
        for level in main.VALID_LOG_LEVELS:
            assert level in readme

class TestOperationalDocumentation:
    """Operational behaviour that users must be told about."""

    def test_readme_documents_command_line_flags(self, readme):
        """--test and --version are user-visible entry points."""
        assert '--test' in readme
        assert '--version' in readme

    def test_readme_documents_fail_fast_behaviour(self, readme):
        """A broken environment aborts startup instead of retrying forever."""
        assert 'Configuration error' in readme

    def test_readme_documents_interval_minimum(self, readme):
        """The minimum accepted INTERVAL_MINUTES is stated."""
        assert str(main.MIN_INTERVAL_MINUTES) in readme

    def test_readme_documents_interval_default(self, readme):
        """The documented default matches the code's default."""
        # Match the environment-variable table row, e.g.
        # | `INTERVAL_MINUTES` | `20` | ... |  -- a looser pattern also matches the
        # prose explaining that 0 is rejected, which is not a default.
        defaults = re.findall(
            r'\|\s*`INTERVAL_MINUTES`\s*\|\s*`(\d+)`\s*\|', readme)
        assert defaults, "INTERVAL_MINUTES default is not documented"
        assert set(defaults) == {'20'}, (
            f"README documents INTERVAL_MINUTES default(s) {defaults}, "
            "but get_interval_minutes() defaults to 20")

    def test_readme_documents_motong_exact_match(self, readme):
        """The motong rule is documented as exact, matching the code.

        The code compares the lowercased release group with ``==``; describing it
        as "contains" (as it once was) would mislead users with
        'motong-encodes'-style group names.
        """
        release_group_rule = [
            line for line in readme.splitlines()
            if 'motong' in line and 'release group' in line.lower()]
        assert release_group_rule, "the motong rule is no longer documented"
        for line in release_group_rule:
            assert 'contains' not in line.lower()

    def test_readme_documents_log_format(self, readme):
        """The log sample uses the format setup_logging actually installs."""
        assert 'INFO - ' in readme

    def test_readme_documents_timeout(self, readme):
        """The request timeout is user-visible behaviour worth stating."""
        assert str(main.REQUEST_TIMEOUT) in readme

class TestTimeoutDocumentation:
    """The timeout constant is documented where operators will look."""

    def test_timeout_value_is_reasonable(self):
        """A timeout of 0 or hundreds of seconds would defeat its purpose."""
        assert 1 <= main.REQUEST_TIMEOUT <= 300

    def test_timeout_documented_in_env_example(self, env_example):
        """Operators reading the env sample learn that calls are bounded."""
        assert 'timeout' in env_example.lower()

def _read_source():
    """Return the application source, for cross-checking documentation."""
    path = os.path.join(REPO_ROOT, 'sonarr-tagger', 'main.py')
    with open(path, encoding='utf-8') as handle:
        return handle.read()
