"""Tests for per-show processing: scoring, tag assignment and specials."""

import io
import logging

import pytest

import main
from conftest import (FakeSonarrAPI, make_episode, make_episode_file,
                      make_show)

TAG_MAP = {
    'negative-score': 1,
    'positive-score': 2,
    'no-score': 3,
    'motong': 4,
    '4k': 5,
    'mixed-release-groups': 6,
}

def make_api_for(show, **kwargs):
    """Build a ``FakeSonarrAPI`` that can serve ``show`` back from both the
    library list and the single-show endpoint.

    ``_update_show_tags`` re-reads a show immediately before writing (so the PUT
    is based on current server state, not a snapshot taken at the start of a
    pass), which means a show must exist in ``api.shows`` for a write to happen
    at all. Registering it here keeps each test focused on tag logic instead of
    repeating that setup. ``show`` is copied so the fixture object the test
    still holds is never the one the code under test mutates.
    """
    return FakeSonarrAPI(shows=[dict(show)], **kwargs)

class _LogCapture(logging.Handler):
    """Collect rendered log messages for assertions."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        """Record the formatted message of a log record."""
        self.messages.append(record.getMessage())

class _LogCapture(logging.Handler):
    """Collect rendered log messages for assertions."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        """Record the formatted message of a log record."""
        self.messages.append(record.getMessage())

def _capture_log_messages(func, *args, **kwargs):
    """Capture log messages by temporarily attaching a handler.

    The root logger is raised to INFO for the duration, because pytest's default
    WARNING level filters INFO records before any handler - including this one -
    ever sees them.
    """
    handler = _LogCapture()
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        func(*args, **kwargs)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
    return handler.messages


class TestScoreTagSelection:
    """get_score_tag maps a custom format score onto a tag name."""

    @pytest.mark.parametrize('score,threshold,expected', [
        (None, 100, 'no-score'),
        (-1, 100, 'negative-score'),
        (-999, 100, 'negative-score'),
        (0, 100, 'no-score'),
        (100, 100, 'no-score'),
        (101, 100, 'positive-score'),
        (500, 100, 'positive-score'),
    ])
    def test_threshold_boundaries(self, score, threshold, expected):
        """The boundary is exclusive: score must exceed the threshold."""
        assert main.get_score_tag(score, threshold) == expected

    def test_none_score_is_no_score(self):
        """A show with no episode files has no score at all."""
        assert main.get_score_tag(None, 100) == 'no-score'

    def test_zero_threshold_makes_positive_scores_positive(self):
        """With threshold 0, any positive score is positive."""
        assert main.get_score_tag(1, 0) == 'positive-score'

    def test_negative_threshold(self):
        """A negative threshold still classifies negative scores first."""
        assert main.get_score_tag(-5, -10) == 'negative-score'

    def test_every_result_is_a_managed_tag(self):
        """Whatever is returned must exist in REQUIRED_TAGS."""
        for score in (None, -1, 0, 101):
            assert main.get_score_tag(score, 100) in main.REQUIRED_TAGS

class TestEpisodeFileProcessing:
    """_process_episode_files derives the per-show facts tags depend on."""

    def _process(self, files, check_mixed=False):
        api = FakeSonarrAPI(episode_files={1: files})
        return main._process_episode_files(api, 1, check_mixed)

    def test_min_score_is_the_lowest_file_score(self):
        """The show's score is its worst episode file."""
        min_score, _, _, _ = self._process([
            make_episode_file(score=200),
            make_episode_file(score=-50),
            make_episode_file(score=100),
        ])
        assert min_score == -50

    def test_single_file_score(self):
        """A single file's score is the show score."""
        min_score, _, _, _ = self._process([make_episode_file(score=7)])
        assert min_score == 7

    def test_no_files_yields_none_score(self):
        """No episode files means no score (-> 'no-score' tag)."""
        min_score, _, _, _ = self._process([])
        assert min_score is None

    def test_none_scores_are_skipped(self):
        """A file with a null score does not become the minimum."""
        min_score, _, _, _ = self._process([
            make_episode_file(score=None),
            make_episode_file(score=15),
        ])
        assert min_score == 15

    def test_all_none_scores_yields_none(self):
        """If every file lacks a score, the show has none."""
        min_score, _, _, _ = self._process([make_episode_file(score=None)])
        assert min_score is None

    def test_4k_detection(self):
        """A 2160p episode file marks the show as 4K."""
        _, has_4k, _, _ = self._process([make_episode_file(resolution=2160)])
        assert has_4k is True

    @pytest.mark.parametrize('resolution', [1080, 720, 480, None])
    def test_non_2160_is_not_4k(self, resolution):
        """Only 2160p counts as 4K."""
        _, has_4k, _, _ = self._process([make_episode_file(resolution=resolution)])
        assert has_4k is False

    def test_one_4k_file_among_many_marks_the_show(self):
        """4K is a show-level fact: any qualifying file is enough."""
        _, has_4k, _, _ = self._process([
            make_episode_file(resolution=1080),
            make_episode_file(resolution=2160),
        ])
        assert has_4k is True

    def test_motong_detection(self):
        """A 'motong' release group marks the show."""
        _, _, has_motong, _ = self._process(
            [make_episode_file(release_group='motong')])
        assert has_motong is True

    @pytest.mark.parametrize('release_group', ['motong', 'MOTONG', 'MoToNg'])
    def test_motong_matching_is_case_insensitive(self, release_group):
        """The release group is lowercased before comparison."""
        _, _, has_motong, _ = self._process(
            [make_episode_file(release_group=release_group)])
        assert has_motong is True

    @pytest.mark.parametrize('release_group', [
        'motong ', ' motong', 'motongs', 'notmotong',
    ])
    def test_motong_matching_is_not_a_substring_search(self, release_group):
        """The comparison is equality, not containment or a trim.

        The README says 'contains', but the code compares the whole lowercased
        value with ``==``: neither padding nor a longer name matches. Pinned here
        so the docs/code drift is caught deliberately rather than by surprise.
        """
        _, _, has_motong, _ = self._process(
            [make_episode_file(release_group=release_group)])
        assert has_motong is False

    @pytest.mark.parametrize('release_group', ['NGP', 'GRP', '', 'motong2'])
    def test_other_release_groups_are_not_motong(self, release_group):
        """Unrelated release groups do not set the flag."""
        _, _, has_motong, _ = self._process(
            [make_episode_file(release_group=release_group)])
        assert has_motong is False

    def test_missing_release_group_is_not_motong(self):
        """A file with no release group field does not crash or match."""
        _, _, has_motong, _ = self._process([{'customFormatScore': 5}])
        assert has_motong is False

    def test_episode_file_fetch_failure_is_contained(self):
        """A RequestException fetching files yields neutral results."""
        api = FakeSonarrAPI(fail_on={'get_episode_files': main.RequestException})
        min_score, has_4k, has_motong, has_mixed = \
            main._process_episode_files(api, 1, False)
        assert (min_score, has_4k, has_motong, has_mixed) == \
            (None, False, False, False)

    def test_missing_quality_field_is_not_4k(self):
        """Malformed quality data must not raise."""
        _, has_4k, _, _ = self._process([{'customFormatScore': 1}])
        assert has_4k is False

class TestMixedReleaseGroups:
    """Detecting seasons that mix release groups."""

    def _process(self, files, check=True):
        api = FakeSonarrAPI(episode_files={1: files})
        return main._process_episode_files(api, 1, check)[3]

    def test_mixed_within_one_season(self):
        """Two release groups in the same season is mixed."""
        assert self._process([
            make_episode_file(season_number=1, release_group='A'),
            make_episode_file(season_number=1, release_group='B'),
        ]) is True

    def test_single_group_per_season_is_not_mixed(self):
        """Different groups across seasons are not mixed."""
        assert self._process([
            make_episode_file(season_number=1, release_group='A'),
            make_episode_file(season_number=2, release_group='B'),
        ]) is False

    def test_same_group_across_seasons_is_not_mixed(self):
        """One group throughout is not mixed."""
        assert self._process([
            make_episode_file(season_number=1, release_group='A'),
            make_episode_file(season_number=2, release_group='A'),
        ]) is False

    def test_specials_are_excluded(self):
        """Season 0 is skipped so specials cannot trigger the tag."""
        assert self._process([
            make_episode_file(season_number=0, release_group='A'),
            make_episode_file(season_number=0, release_group='B'),
        ]) is False

    def test_empty_release_group_counts_as_a_group(self):
        """A blank release group is a valid distinct value."""
        assert self._process([
            make_episode_file(season_number=1, release_group=''),
            make_episode_file(season_number=1, release_group='A'),
        ]) is True

    def test_disabled_check_is_skipped(self):
        """With the feature off, no grouping work happens."""
        assert self._process([
            make_episode_file(season_number=1, release_group='A'),
            make_episode_file(season_number=1, release_group='B'),
        ], check=False) is False

    def test_mixed_across_multiple_seasons_detected(self):
        """One mixed season among clean seasons still sets the flag."""
        assert self._process([
            make_episode_file(season_number=1, release_group='A'),
            make_episode_file(season_number=2, release_group='B'),
            make_episode_file(season_number=2, release_group='C'),
        ]) is True

    def test_missing_season_number_is_skipped(self):
        """Files without a season number cannot form a season group."""
        assert self._process([
            {'customFormatScore': 1, 'releaseGroup': 'A'},
            {'customFormatScore': 1, 'releaseGroup': 'B'},
        ]) is False

class TestTagUpdates:
    """_update_show_tags decides whether a show's tags change."""

    def _config(self, **overrides):
        config = {
            'tag_motong_enabled': False,
            'tag_4k_enabled': False,
            'tag_mixed_release_groups_enabled': False,
            'monitor_existing_specials_enabled': False,
        }
        config.update(overrides)
        return config

    def _run(self, api, show, min_score=0, threshold=100, has_4k=False,
             has_motong=False, has_mixed=False, config=None,
             has_mixed_release_groups=None):
        config = config or self._config()
        if has_mixed_release_groups is not None:
            has_mixed = has_mixed_release_groups
        # _update_show_tags re-reads the show before writing, so the show must be
        # reachable from the fake's library. Registering it keeps each test's
        # setup focused on the tag logic under test.
        if not any(s['id'] == show['id'] for s in api.shows):
            api.shows.append(dict(show))
        data = main.TagUpdateData(
            sonarr=main.SonarrContext(api=api, show=show, config=config),
            tags=main.TagContext(current_tags=set(show.get('tags', [])),
                                 tag_map=dict(TAG_MAP)),
            scores=main.ScoreContext(min_score=min_score,
                                     score_threshold=threshold),
            has_4k=has_4k,
            has_motong=has_motong,
            has_mixed_release_groups=has_mixed,
        )
        return main._update_show_tags(data)

    def test_untagged_show_gets_score_tag(self):
        """A show with no tags receives its score tag."""
        api = FakeSonarrAPI()
        assert self._run(api, make_show(tags=[])) is True
        assert api.updates[0][1]['tags'] == [3]  # no-score (score 0, threshold 100)

    def test_positive_score_tag_applied(self):
        """A high-scoring show gets positive-score."""
        api = FakeSonarrAPI()
        self._run(api, make_show(), min_score=500)
        assert TAG_MAP['positive-score'] in api.updates[0][1]['tags']

    def test_negative_score_tag_applied(self):
        """A negative-scoring show gets negative-score."""
        api = FakeSonarrAPI()
        self._run(api, make_show(), min_score=-5)
        assert TAG_MAP['negative-score'] in api.updates[0][1]['tags']

    def test_managed_tags_are_stripped_and_recomputed(self):
        """Every managed tag is removed before the desired state is applied."""
        api = FakeSonarrAPI()
        show = make_show(tags=[TAG_MAP['positive-score'], TAG_MAP['motong']])
        self._run(api, show, min_score=0)
        assert api.updates[0][1]['tags'] == [TAG_MAP['no-score']]

    def test_unmanaged_tags_are_preserved(self):
        """Tags this tool does not own are left alone."""
        api = FakeSonarrAPI()
        show = make_show(tags=[99])
        self._run(api, show, min_score=0)
        assert 99 in api.updates[0][1]['tags']

    def test_no_update_when_tags_already_correct(self):
        """An already-correct show is not PUT (avoids pointless writes)."""
        api = FakeSonarrAPI()
        show = make_show(tags=[TAG_MAP['no-score']])
        assert self._run(api, show, min_score=0) is False
        assert api.calls['update_show'] == 0

    @pytest.mark.parametrize('flag,has_flag,tag_name', [
        ('tag_motong_enabled', 'has_motong', 'motong'),
        ('tag_4k_enabled', 'has_4k', '4k'),
        ('tag_mixed_release_groups_enabled', 'has_mixed_release_groups',
         'mixed-release-groups'),
    ])
    def test_flag_and_condition_apply_tag(self, flag, has_flag, tag_name):
        """The tag is applied only when enabled AND the condition holds."""
        api = FakeSonarrAPI()
        self._run(api, make_show(), min_score=0,
                  config=self._config(**{flag: True}), **{has_flag: True})
        assert TAG_MAP[tag_name] in api.updates[0][1]['tags']

    @pytest.mark.parametrize('flag,has_flag,tag_name', [
        ('tag_motong_enabled', 'has_motong', 'motong'),
        ('tag_4k_enabled', 'has_4k', '4k'),
        ('tag_mixed_release_groups_enabled', 'has_mixed_release_groups',
         'mixed-release-groups'),
    ])
    def test_condition_without_flag_applies_nothing(self, flag, has_flag,
                                                    tag_name):
        """Detected but not enabled: the tag must not be applied."""
        api = FakeSonarrAPI()
        self._run(api, make_show(), min_score=0,
                  config=self._config(**{flag: False}), **{has_flag: True})
        assert TAG_MAP[tag_name] not in api.updates[0][1]['tags']

    @pytest.mark.parametrize('flag,has_flag', [
        ('tag_motong_enabled', 'has_motong'),
        ('tag_4k_enabled', 'has_4k'),
    ])
    def test_flag_without_condition_applies_nothing(self, flag, has_flag):
        """Enabled but not detected: the tag must not be applied."""
        api = FakeSonarrAPI()
        self._run(api, make_show(), min_score=0,
                  config=self._config(**{flag: True}), **{has_flag: False})
        applied = api.updates[0][1]['tags']
        assert set(applied) == {TAG_MAP['no-score']}

    def test_disabling_a_flag_removes_its_existing_tag(self):
        """Turning a flag off strips the tag from shows that still have it.

        This is the documented consequence of the strip-then-reapply design: the
        tag is managed, so it is removed when not actively desired.
        """
        api = FakeSonarrAPI()
        show = make_show(tags=[TAG_MAP['motong'], TAG_MAP['no-score']])
        self._run(api, show, min_score=0,
                  config=self._config(tag_motong_enabled=False))
        assert TAG_MAP['motong'] not in api.updates[0][1]['tags']

class TestNoNPlusOneRequests:
    """Tag lookups must not scale with the number of tags on a show.

    The original implementation called get_tags() inside a comprehension over the
    show's existing tags, so a show with N managed tags caused N HTTP requests -
    per show, every cycle. That is an N+1 that would hammer a large Sonarr.
    """

    def _run(self, api, show):
        # The re-read before the write means the show must exist in the fake's
        # library (see the sibling helper in TestTagUpdates).
        if not any(s['id'] == show['id'] for s in api.shows):
            api.shows.append(dict(show))
        data = main.TagUpdateData(
            sonarr=main.SonarrContext(api=api, show=show,
                                      config={'tag_motong_enabled': False,
                                              'tag_4k_enabled': False,
                                              'tag_mixed_release_groups_enabled':
                                                  False,
                                              'monitor_existing_specials_enabled':
                                                  False}),
            tags=main.TagContext(current_tags=set(show.get('tags', [])),
                                 tag_map=dict(TAG_MAP)),
            scores=main.ScoreContext(min_score=0, score_threshold=100),
            has_4k=False,
            has_motong=False,
            has_mixed_release_groups=False,
        )
        return main._update_show_tags(data)

    @pytest.mark.parametrize('tag_count', [1, 6, 20])
    def test_no_tag_fetch_during_update(self, tag_count):
        """No get_tags() call is made while computing a show's new tags."""
        api = FakeSonarrAPI()
        show = make_show(tags=list(TAG_MAP.values())[:tag_count])
        self._run(api, show)
        assert api.calls['get_tags'] == 0, (
            f"_update_show_tags made {api.calls['get_tags']} get_tags() calls "
            "for a show with "
            f"{tag_count} tags - the N+1 is back")

    def test_managed_set_is_derived_from_required_tags(self, full_tag_map):
        """The strip set comes from the managed tag names, not the whole map.

        ``ensure_required_tags()`` returns every tag Sonarr knows about, so
        deriving the managed ids from that map erased unrelated tags
        ('requested', 'potential-delete', ...) from every show, every pass. The
        unmanaged ids used here are present in ``full_tag_map`` on purpose - that
        is the production shape, and it is what makes this test fail if the strip
        set is derived from the map's values again.
        """
        api = FakeSonarrAPI()
        show = make_show(tags=[full_tag_map['requested'], TAG_MAP['motong'],
                               full_tag_map['potential-delete']])
        # Registered so the pre-write re-read can resolve the show.
        api.shows.append(dict(show))
        data = main.TagUpdateData(
            sonarr=main.SonarrContext(api=api, show=show,
                                      config={'tag_motong_enabled': False,
                                              'tag_4k_enabled': False,
                                              'tag_mixed_release_groups_enabled':
                                                  False,
                                              'monitor_existing_specials_enabled':
                                                  False}),
            tags=main.TagContext(current_tags=set(show['tags']),
                                 tag_map=dict(full_tag_map)),
            scores=main.ScoreContext(min_score=0, score_threshold=100),
            has_4k=False,
            has_motong=False,
            has_mixed_release_groups=False,
        )
        assert main._update_show_tags(data) is True
        assert api.calls['get_tags'] == 0
        updated = api.updates[0][1]['tags']
        assert full_tag_map['requested'] in updated, "unmanaged tag stripped"
        assert full_tag_map['potential-delete'] in updated, (
            "unmanaged tag stripped")
        assert TAG_MAP['no-score'] in updated
        assert TAG_MAP['motong'] not in updated

class TestSpecialsMonitoring:
    """monitor_existing_specials monitors downloaded season-0 episodes."""

    def _config(self, enabled=True):
        return {'monitor_existing_specials_enabled': enabled}

    def test_disabled_by_default(self):
        """With the feature off, no episode API calls are made at all."""
        api = FakeSonarrAPI(episodes={1: [make_episode(season_number=0)]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config(enabled=False)) == 0
        assert api.calls['get_episodes'] == 0
        assert api.calls['update_episode'] == 0

    def test_unmonitored_special_with_file_is_monitored(self):
        """A downloaded but unmonitored special becomes monitored."""
        api = FakeSonarrAPI(episodes={1: [
            make_episode(episode_id=5, season_number=0, has_file=True,
                         monitored=False)]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 1
        assert api.episode_updates[0][1]['monitored'] is True

    def test_already_monitored_special_is_untouched(self):
        """No redundant writes for correct episodes."""
        api = FakeSonarrAPI(episodes={1: [
            make_episode(season_number=0, has_file=True, monitored=True)]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0
        assert api.calls['update_episode'] == 0

    def test_special_without_file_is_untouched(self):
        """An episode that is not on disk has nothing to monitor."""
        api = FakeSonarrAPI(episodes={1: [
            make_episode(season_number=0, has_file=False, monitored=False)]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0

    def test_regular_episodes_are_untouched(self):
        """Only season 0 is considered a special."""
        api = FakeSonarrAPI(episodes={1: [
            make_episode(season_number=1, has_file=True, monitored=False)]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0

    def test_counts_multiple_specials(self):
        """Every qualifying special is counted."""
        api = FakeSonarrAPI(episodes={1: [
            make_episode(episode_id=1, season_number=0, has_file=True),
            make_episode(episode_id=2, season_number=0, has_file=True),
            make_episode(episode_id=3, season_number=0, has_file=False),
            make_episode(episode_id=4, season_number=1, has_file=True),
        ]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 2

    def test_update_failure_is_not_counted(self):
        """A failed episode update does not inflate the count."""
        api = FakeSonarrAPI(
            episodes={1: [make_episode(season_number=0, has_file=True)]},
            update_result=False)
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0

    def test_fetch_failure_is_contained(self):
        """A failure listing episodes yields zero, not an exception."""
        api = FakeSonarrAPI(fail_on={'get_episodes': main.RequestException})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0

    def test_monitored_key_defaults_to_true_when_absent(self):
        """A missing 'monitored' key behaves as already-monitored (no write)."""
        episode = make_episode(season_number=0, has_file=True)
        del episode['monitored']
        api = FakeSonarrAPI(episodes={1: [episode]})
        assert main.monitor_existing_specials(
            api, make_show(), self._config()) == 0

