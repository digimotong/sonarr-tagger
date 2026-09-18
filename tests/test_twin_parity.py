"""Divergence guard for the two sibling containers.

sonarr-tagger and radarr-tagger are near-duplicates: same poll loop, same
config validation, same tag-management rules, same N+1 history. They are
maintained as separate repositories and auto-deployed independently (WUD), so a
hardening fix applied to one silently misses the other - which is exactly how
this sibling ended up without the empty-API-key guard.

A full shared-library refactor was considered and rejected: it would couple two
independently deployed images, so a bad release of one would break the other.
This test is the cheaper substitute. It asserts that the *logic* which must
agree really does agree, by comparing normalised source text, while explicitly
allowing the parts that are legitimately different (the product name and URL
environment variables).

It also compares the *documentation scaffolding* - the guard names in
``tests/test_docs.py``, the notes in ``.env.example`` and the README headings -
because that is where the drift actually happened: the sibling's env sample lost
a timeout note and its doc-test file fell six guards behind this one, and a
source-only comparison could not see either.

If this fails, the fix is to port the change to the sibling - not to relax the
assertion. The expected shape of each twin is pinned below so that a silent
rewrite of one side is caught rather than rubber-stamped.
"""

import os
import re

import pytest

import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIBLING_ROOT = os.path.join(os.path.dirname(REPO_ROOT), 'radarr-tagger')
SIBLING_PATH = os.path.join(SIBLING_ROOT, 'radarr-tagger', 'main.py')
SIBLING_DOC_TESTS_PATH = os.path.join(SIBLING_ROOT, 'tests', 'test_docs.py')
SIBLING_ENV_EXAMPLE_PATH = os.path.join(SIBLING_ROOT, '.env.example')
SIBLING_README_PATH = os.path.join(SIBLING_ROOT, 'README.md')

# This module lives in the sonarr repo; the sibling only exists in a combined
# checkout (the local /data/repos layout). Skip rather than fail when CI checks
# out a single repository.
pytestmark = pytest.mark.skipif(
    not os.path.exists(SIBLING_PATH),
    reason="sibling repository not checked out - parity check needs both repos")


@pytest.fixture(scope='module')
def sibling_source():
    """Return the sibling module's source text."""
    with open(SIBLING_PATH, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture(scope='module')
def own_source():
    """Return this module's own source text."""
    with open(os.path.abspath(main.__file__), encoding='utf-8') as handle:
        return handle.read()


def _normalise(source):
    """Strip the legitimately-different parts and the noise of formatting.

    Product names, the noun for the managed resource (movie/show), the name of
    the managed-tag constant and the version string are expected to differ;
    comments and blank lines are not part of the behaviour being compared.
    Everything else must match character for character after this.
    """
    text = source.replace('Radarr', 'PRODUCT').replace('radarr', 'product')
    text = text.replace('Sonarr', 'PRODUCT').replace('sonarr', 'product')
    # The resource noun differs by product but the logic around it must not.
    text = re.sub(r'\bmovies?\b', 'RESOURCE', text, flags=re.IGNORECASE)
    text = re.sub(r'\bshows?\b', 'RESOURCE', text, flags=re.IGNORECASE)
    # Movies use MANAGED_TAGS, series use REQUIRED_TAGS; same role.
    text = text.replace('MANAGED_TAGS', 'RESOURCE_TAGS')
    text = text.replace('REQUIRED_TAGS', 'RESOURCE_TAGS')
    text = re.sub(r'VERSION = "[^"]*"', 'VERSION = "X"', text)
    # Each product names its own API-key variable in the message.
    text = re.sub(r'\b[A-Z]+_API_KEY\b', 'PRODUCT_API_KEY', text)
    # Parameter names echo the resource noun (fresh_movie/fresh_show).
    text = re.sub(r'\bfresh_(?:movie|show)\b', 'fresh_resource', text)
    # Docstrings explain the same rule in each product's own vocabulary; the
    # executable code below them is what must stay in lockstep.
    text = re.sub(r'""".*?"""', '"""..."""', text, flags=re.DOTALL)
    text = re.sub(r"'''.*?'''", "'''...'''", text, flags=re.DOTALL)
    text = re.sub(r'#.*', '', text)              # drop comments
    text = re.sub(r'\s+', '', text)              # drop all whitespace
    return text


def _normalise_comment(line):
    """Normalise a documentation comment line for twin comparison.

    ``_normalise`` is built for code: it drops comments and all whitespace, so
    every comment line collapses to the empty string and comparing them would
    always pass. This keeps the words, folds case and runs of spaces, and
    substitutes the product names so only genuine differences remain.
    """
    text = line.lstrip('#').strip()
    for product in ('Radarr', 'radarr', 'Sonarr', 'sonarr'):
        text = text.replace(product, 'product')
    text = re.sub(r'\b[Mm]ovies?\b', 'resource', text)
    text = re.sub(r'\b[Ss]hows?\b', 'resource', text)
    return re.sub(r'\s+', ' ', text).lower()


def _extract(source, name):
    """Return the source of a top-level ``def name`` or ``class name`` block.

    The block ends at the next line that starts in column 0 - either another
    top-level statement or the end of file. Matching on the next ``def``/``class``
    alone would swallow trailing module-level assignments (the sibling has
    ``VERSION`` right after ``get_score_tag``), which is unrelated to the
    function body being compared.
    """
    pattern = re.compile(
        rf'^(?:def|class) {re.escape(name)}\b.*?(?=^\S|\Z)',
        re.MULTILINE | re.DOTALL)
    match = pattern.search(source)
    assert match, f"{name} not found in source"
    return match.group(0)

class TestSharedLogicIsIdentical:
    """Functions that must behave identically in both containers."""

    @pytest.mark.parametrize('name', [
        'get_score_tag',
        '_raise_on_auth_failure',
        '_merge_fresh_tags',
        'AuthenticationError',
        'ensure_required_tags',
        'parse_args',
        'setup_logging',
    ])
    def test_normalised_source_matches_sibling(self, own_source,
                                               sibling_source, name):
        """The shared helper is byte-identical once normalised."""
        assert _normalise(_extract(own_source, name)) == \
            _normalise(_extract(sibling_source, name)), (
                f"{name}() has diverged from radarr-tagger. Port the change to "
                "the sibling repository (or apply it here if the sibling is "
                "ahead) - do not relax this test.")

    def test_poll_loop_retry_policy_matches(self, own_source, sibling_source):
        """Both loops retry transient failures for 5 minutes."""
        for source in (own_source, sibling_source):
            assert 'time.sleep(300)' in source
            assert 'Retrying in 5 minutes' in source

class TestRetryPolicyHasNotSilentlyChanged:
    """Pin the values the parity check compares, so a twin rewrite is visible.

    Without this, someone could "fix" a divergence by changing both sides to
    something equally wrong (for example dropping the fatal auth branch) and the
    parity assertions above would still pass.
    """

    def test_auth_failure_exits_instead_of_retrying(self, own_source,
                                                    sibling_source):
        """Each loop must treat a rejected key as fatal."""
        for source in (own_source, sibling_source):
            assert 'except AuthenticationError' in source, (
                'the fatal auth branch is missing - a rejected key would be '
                'retried forever again')
            assert 'sys.exit(1)' in source

    def test_retry_delay_is_five_minutes(self, own_source, sibling_source):
        """The transient-failure delay stays 300 seconds on both sides."""
        for source in (own_source, sibling_source):
            assert 'time.sleep(300)' in source

    def test_pre_write_reread_is_present_on_both_sides(self, own_source,
                                                       sibling_source):
        """Both sides re-read the resource before writing it back.

        This is the stale-write guard; if one side loses it, that container
        silently reverts user tag edits made during a pass.
        """
        assert 'api.get_show(' in own_source
        assert 'api.get_movie(' in sibling_source
        for source in (own_source, sibling_source):
            assert '_merge_fresh_tags' in source

class TestProductSpecificExpectations:
    """The halves that legitimately differ must still match each other's shape."""

    def test_managed_tag_lists_share_the_score_tags(self):
        """Both tools manage the same three score tags."""
        score_tags = {'negative-score', 'positive-score', 'no-score'}
        assert score_tags <= set(main.REQUIRED_TAGS)

    def test_required_env_vars_prefix_matches_product(self):
        """Each container reads its own product's variables."""
        assert set(main.REQUIRED_ENV_VARS) == {'SONARR_URL', 'SONARR_API_KEY'}

    def test_season_filter_optimisation_is_retained(self, own_source):
        """The season-0 filter is Sonarr-only; it must not be lost."""
        assert 'season_number=0' in own_source


class TestDocumentationScaffoldingMatches:
    """The docs and their guards are twins too, and must be kept in lockstep.

    Comparing ``main.py`` alone missed real drift: one env sample carried a
    timeout note the other had lost, and one ``test_docs.py`` was six guards
    ahead of the other's. Structure is compared rather than prose (a heading, a
    variable name, a test name), and nothing here asserts wording, so both
    files stay free to be rewritten.
    """

    def _read(self, path):
        """Return the text of a sibling file."""
        with open(path, encoding='utf-8') as handle:
            return handle.read()

    def _guard_names(self, source):
        """Return the test and class names defined in a test_docs.py file."""
        return (
            set(re.findall(r'^class (\w+)', source, re.MULTILINE)),
            set(re.findall(r'^\s+def (test_\w+)', source, re.MULTILINE)),
        )

    def test_doc_guard_names_match(self):
        """Both doc-test files enforce the same set of claims."""
        own = self._guard_names(self._read(os.path.join(
            REPO_ROOT, 'tests', 'test_docs.py')))
        sibling = self._guard_names(self._read(SIBLING_DOC_TESTS_PATH))
        assert own == sibling, (
            "tests/test_docs.py and the sibling's no longer guard the same "
            "claims. Port the missing guards rather than relaxing this check.\n"
            f"classes only here: {sorted(own[0] - sibling[0])}, "
            f"only there: {sorted(sibling[0] - own[0])}\n"
            f"tests only here: {sorted(own[1] - sibling[1])}, "
            f"only there: {sorted(sibling[1] - own[1])}")

    def test_env_example_notes_match(self):
        """The two sample env files explain the same things.

        Comment lines only: the values themselves differ by product, which is
        exactly why the product name is normalised away before comparing.
        """
        def notes(text):
            return sorted(
                _normalise_comment(line) for line in text.splitlines()
                if line.startswith('#'))

        own = notes(self._read(os.path.join(REPO_ROOT, '.env.example')))
        sibling = notes(self._read(SIBLING_ENV_EXAMPLE_PATH))
        missing_here = [line for line in sibling if line not in own]
        missing_there = [line for line in own if line not in sibling]
        assert own == sibling, (
            "The .env.example files have diverged.\n"
            f"missing here: {missing_here}\nmissing in the sibling: "
            f"{missing_there}")

    def test_readme_section_headings_match(self):
        """Both READMEs are organised the same way."""
        def headings(text):
            return [_normalise_comment(line) for line in text.splitlines()
                    if line.startswith('#')]

        own = headings(self._read(os.path.join(REPO_ROOT, 'README.md')))
        sibling = headings(self._read(SIBLING_README_PATH))
        assert own == sibling, (
            "The READMEs no longer have matching section headings.\n"
            f"here: {own}\nthere: {sibling}")
