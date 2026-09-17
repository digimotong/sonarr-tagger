#!/usr/bin/env python3
"""
Sonarr Tag Updater
Fetches shows from Sonarr API and updates tags based on episode scores.
"""

import os
import sys
import argparse
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
import requests
from requests.exceptions import RequestException

# Single source of truth for the tags this tool manages (creates and assigns).
# Any tag in this list is stripped from every show before the current desired
# state is applied, and created on demand when missing from Sonarr.
# NOTE: 'motong', '4k' and 'mixed-release-groups' are only re-applied when
# TAG_MOTONG / TAG_4K / TAG_MIXED_RELEASE_GROUPS are enabled, so disabling a
# flag also removes that tag from all shows.
REQUIRED_TAGS = [
    'negative-score',
    'positive-score',
    'no-score',
    'motong',
    '4k',
    'mixed-release-groups'
]

# HTTP calls block indefinitely when no timeout is supplied, which would leave the
# long-running update loop wedged forever on a half-open connection.
REQUEST_TIMEOUT = 30

# Environment variables that must be present and non-empty for the tool to run.
REQUIRED_ENV_VARS = ('SONARR_URL', 'SONARR_API_KEY')

# Bounds for INTERVAL_MINUTES. A zero/negative interval turns the poll loop into
# an unbounded busy loop (time.sleep(0) returns instantly), and an absurd value
# silently stops the container from ever updating again. Reject both at startup.
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 525_600  # one year

# Log levels accepted in LOG_LEVEL, as a case-insensitive lookup.
VALID_LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

class SonarrAPI:
    """Client for Sonarr API interactions"""

    def __init__(self, base_url: str, api_key: str,
                 session: requests.Session = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = session if session is not None else requests.Session()
        if hasattr(self.session, 'headers'):
            self.session.headers.update({
                'X-Api-Key': self.api_key,
                'Accept': 'application/json'
            })

    def get_shows(self) -> List[Dict]:
        """Fetch all shows from Sonarr"""
        endpoint = f"{self.base_url}/api/v3/series"
        try:
            response = self.session.get(endpoint, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error("Failed to fetch shows: %s", str(e))
            raise

    def get_tags(self) -> List[Dict]:
        """Fetch all tags from Sonarr"""
        endpoint = f"{self.base_url}/api/v3/tag"
        try:
            response = self.session.get(endpoint, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error("Failed to fetch tags: %s", str(e))
            raise

    def create_tag(self, label: str) -> Dict:
        """Create a new tag in Sonarr"""
        endpoint = f"{self.base_url}/api/v3/tag"
        try:
            response = self.session.post(endpoint, json={
                'label': label
            }, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error("Failed to create tag '%s': %s", label, str(e))
            raise

    def get_episode_files(self, series_id: int) -> List[Dict]:
        """Fetch all episode files for a show from Sonarr"""
        endpoint = f"{self.base_url}/api/v3/episodefile?seriesId={series_id}"
        try:
            response = self.session.get(endpoint, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error("Failed to fetch episode files for series %s: %s", series_id, str(e))
            raise

    def update_show(self, series_id: int, series_data: Dict) -> bool:
        """Update a show in Sonarr"""
        endpoint = f"{self.base_url}/api/v3/series/{series_id}"
        try:
            response = self.session.put(endpoint, json=series_data,
                                        timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return True
        except RequestException as e:
            logging.error(
                "Failed to update show %s. Response: %s. Error: %s",
                series_id,
                response.text if 'response' in locals() else '',
                str(e))
            return False

    def get_episodes(self, series_id: int) -> List[Dict]:
        """Fetch all episodes for a show from Sonarr"""
        endpoint = f"{self.base_url}/api/v3/episode?seriesId={series_id}"
        try:
            response = self.session.get(endpoint, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error("Failed to fetch episodes for series %s: %s", series_id, str(e))
            raise

    def update_episode(self, episode_id: int, episode_data: Dict) -> bool:
        """Update an episode in Sonarr"""
        endpoint = f"{self.base_url}/api/v3/episode/{episode_id}"
        try:
            response = self.session.put(endpoint, json=episode_data,
                                        timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return True
        except RequestException as e:
            logging.error(
                "Failed to update episode %s. Response: %s. Error: %s",
                episode_id,
                response.text if 'response' in locals() else '',
                str(e))
            return False

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Sonarr Tag Updater')
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run in test mode (only process first 5 shows)')
    parser.add_argument(
        '--version',
        action='store_true',
        help='Show version and exit')
    return parser.parse_args()

def get_log_level() -> str:
    """Return the validated LOG_LEVEL, falling back to INFO.

    An unrecognised level would otherwise reach ``logging.basicConfig`` and raise
    a bare ``ValueError`` from deep inside the stdlib; rejecting it here names the
    offending variable and the accepted values.
    """
    raw_level = os.getenv('LOG_LEVEL', 'INFO').strip()
    level = raw_level.upper()
    if level not in VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid LOG_LEVEL {raw_level!r}. Expected one of: "
            f"{', '.join(VALID_LOG_LEVELS)}")
    return level

def get_interval_minutes() -> int:
    """Return the validated INTERVAL_MINUTES, defaulting to 20.

    Guards against the two ways this value silently breaks the poll loop: a
    non-positive value makes ``time.sleep()`` return immediately (a busy loop that
    hammers the Sonarr API), and a non-integer or absurd value either crashes the
    container on startup or stops it from ever updating at full speed again.
    """
    raw_interval = os.getenv('INTERVAL_MINUTES', '20').strip()
    try:
        interval = int(raw_interval)
    except ValueError as exc:
        raise ValueError(
            f"Invalid INTERVAL_MINUTES {raw_interval!r}. Expected an integer "
            f"between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}.") from exc

    if not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
        raise ValueError(
            f"Invalid INTERVAL_MINUTES {interval}. Expected an integer between "
            f"{MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}.")

    return interval

def get_config_from_env():
    """Load configuration from environment variables"""
    # Fail fast with a message that names the culprits: indexing os.environ directly
    # raised an unhelpful KeyError traceback, and an empty value was only caught
    # later by the explicit check below with a vaguer message.
    missing = [name for name in REQUIRED_ENV_VARS
               if not os.getenv(name, '').strip()]
    if missing:
        raise ValueError(
            "Missing required environment variables: "
            + ", ".join(f"{name} must be set" for name in missing))

    try:
        score_threshold = int(os.getenv('SCORE_THRESHOLD', '100'))
    except ValueError as exc:
        raise ValueError(
            f"Invalid SCORE_THRESHOLD {os.getenv('SCORE_THRESHOLD')!r}. "
            "Expected an integer.") from exc

    config = {
        'sonarr_url': os.environ['SONARR_URL'],
        'sonarr_api_key': os.environ['SONARR_API_KEY'],
        'log_level': get_log_level(),
        'score_threshold': score_threshold,
        'interval_minutes': get_interval_minutes(),
        'tag_motong_enabled': os.getenv('TAG_MOTONG', 'false').lower() == 'true',
        'tag_4k_enabled': os.getenv('TAG_4K', 'false').lower() == 'true',
        'tag_mixed_release_groups_enabled': os.getenv(
            'TAG_MIXED_RELEASE_GROUPS', 'false'
        ).lower() == 'true',
        'monitor_existing_specials_enabled': os.getenv(
            'MONITOR_EXISTING_SPECIALS', 'false'
        ).lower() == 'true'
    }

    logging.debug("Config loaded from environment successfully")
    return config

def get_score_tag(score: int, threshold: int) -> str:
    """Determine the appropriate score tag based on customFormatScore"""
    if score is None:
        return "no-score"
    if score < 0:
        return "negative-score"
    if score > threshold:
        return "positive-score"
    return "no-score"

VERSION = "1.0.8"

@dataclass
class SonarrContext:
    """Container for Sonarr-related parameters"""
    api: SonarrAPI
    show: Dict
    config: Dict

@dataclass
class TagContext:
    """Container for tag-related parameters"""
    current_tags: Set[int]
    tag_map: Dict[str, int]

@dataclass
class ScoreContext:
    """Container for score-related parameters"""
    min_score: Optional[int]
    score_threshold: int

@dataclass
class TagUpdateData:
    """Container for tag update operation parameters"""
    sonarr: SonarrContext
    tags: TagContext
    scores: ScoreContext
    has_4k: bool
    has_motong: bool
    has_mixed_release_groups: bool

def _process_episode_files(
        api: SonarrAPI,
        show_id: int,
        check_mixed_release_groups: bool = False
) -> tuple:
    """Process episode files and return min_score, has_4k, has_motong, has_mixed_release_groups"""
    min_score = None
    has_4k = False
    has_motong = False
    has_mixed_release_groups = False

    try:
        episode_files = api.get_episode_files(show_id)

        if check_mixed_release_groups:
            # Group episodes by season and track release groups per season
            season_release_groups = {}
            for ep_file in episode_files:
                season_number = ep_file.get('seasonNumber')
                if season_number is None or season_number == 0:  # Skip Specials (season 0)
                    continue

                release_group = ep_file.get('releaseGroup', '')
                # Treat empty string as a valid release group
                if season_number not in season_release_groups:
                    season_release_groups[season_number] = set()
                season_release_groups[season_number].add(release_group)

            # Check if any season has more than one unique release group
            for release_groups in season_release_groups.values():
                if len(release_groups) > 1:
                    has_mixed_release_groups = True
                    break

        for ep_file in episode_files:
            ep_score = ep_file.get('customFormatScore')
            if min_score is None or (ep_score is not None and ep_score < min_score):
                min_score = ep_score

            quality = ep_file.get('quality', {})
            if quality.get('quality', {}).get('resolution') == 2160:
                has_4k = True

            if ep_file.get('releaseGroup', '').lower() == 'motong':
                has_motong = True
    except RequestException:
        logging.warning("Failed to get episode files for show %s", show_id)

    return min_score, has_4k, has_motong, has_mixed_release_groups

def _update_show_tags(data: TagUpdateData) -> bool:
    """Update tags for a show based on collected data"""
    show_update = data.sonarr.show.copy()
    # Fetch tags once instead of calling get_tags() for every existing tag on the
    # show (that was an N+1: one HTTP round-trip per tag, per show, every cycle).
    managed_tag_ids = set(data.tags.tag_map.values())
    new_tag_ids = [tag_id for tag_id in data.tags.current_tags
                  if tag_id not in managed_tag_ids]

    new_tag_name = get_score_tag(data.scores.min_score, data.scores.score_threshold)
    new_tag_ids.append(data.tags.tag_map[new_tag_name])

    if data.has_motong and data.sonarr.config['tag_motong_enabled']:
        new_tag_ids.append(data.tags.tag_map['motong'])
    if data.has_4k and data.sonarr.config['tag_4k_enabled']:
        new_tag_ids.append(data.tags.tag_map['4k'])
    if data.has_mixed_release_groups and data.sonarr.config['tag_mixed_release_groups_enabled']:
        new_tag_ids.append(data.tags.tag_map['mixed-release-groups'])

    if set(new_tag_ids) != data.tags.current_tags:
        show_update['tags'] = new_tag_ids
        return data.sonarr.api.update_show(data.sonarr.show['id'], show_update)
    return False

def process_show_tags(
        api: SonarrAPI,
        show: Dict,
        tag_map: Dict,
        score_threshold: int,
        config: Dict) -> bool:
    """Process and update tags for a single show"""
    current_tags = set(show.get('tags', []))
    min_score, has_4k, has_motong, has_mixed_release_groups = _process_episode_files(
        api, show['id'], config['tag_mixed_release_groups_enabled']
    )
    update_data = TagUpdateData(
        sonarr=SonarrContext(api=api, show=show, config=config),
        tags=TagContext(current_tags=current_tags, tag_map=tag_map),
        scores=ScoreContext(min_score=min_score, score_threshold=score_threshold),
        has_4k=has_4k,
        has_motong=has_motong,
        has_mixed_release_groups=has_mixed_release_groups
    )
    return _update_show_tags(update_data)

def monitor_existing_specials(api: SonarrAPI, show: Dict, config: Dict) -> int:
    """Check specials (season 0) for a show and update episodes that exist on disk to monitored.
    
    Returns the number of episodes updated.
    """
    if not config['monitor_existing_specials_enabled']:
        return 0

    try:
        episodes = api.get_episodes(show['id'])
    except RequestException:
        logging.warning("Failed to get episodes for show %s", show['id'])
        return 0

    updated_count = 0
    for episode in episodes:
        # Filter for season 0 episodes that have a file but are not monitored
        if (episode.get('seasonNumber') == 0 and
            episode.get('hasFile', False) and
            not episode.get('monitored', True)):

            # Create a copy of the episode data with monitored set to True
            episode_update = episode.copy()
            episode_update['monitored'] = True

            if api.update_episode(episode['id'], episode_update):
                updated_count += 1
                logging.debug(
                    "Updated special episode %s (S%02dE%02d) to monitored for show %s",
                    episode['id'],
                    episode.get('seasonNumber', 0),
                    episode.get('episodeNumber', 0),
                    show['title']
                )
            else:
                logging.warning(
                    "Failed to update special episode %s for show %s",
                    episode['id'],
                    show['title']
                )

    if updated_count > 0:
        logging.info(
            "Updated %s special episode(s) to monitored for show %s",
            updated_count,
            show['title']
        )

    return updated_count

def ensure_required_tags(api: SonarrAPI) -> Dict:
    """Ensure required tags exist and return tag name to ID mapping"""
    all_tags = api.get_tags()
    tag_map = {tag['label']: tag['id'] for tag in all_tags}

    for tag in REQUIRED_TAGS:
        if tag not in tag_map:
            logging.info("Creating missing tag: %s", tag)
            new_tag = api.create_tag(tag)
            tag_map[tag] = new_tag['id']

    return tag_map

def run_once(api: SonarrAPI, config: Dict, test_mode: bool = False) -> int:
    """Run a single update pass over all shows and return the number of shows updated.

    Extracted from ``main()`` so a full cycle can be exercised in tests without the
    surrounding ``while True`` loop. ``test_mode`` mirrors the ``--test`` flag and
    limits the pass to the first 5 shows.
    """
    tag_map = ensure_required_tags(api)
    shows = api.get_shows()

    if test_mode:
        shows = shows[:5]
        logging.info("TEST MODE: Processing first 5 shows only")

    updated_count = 0
    specials_updated_count = 0

    for show in shows:
        if process_show_tags(api, show, tag_map, config['score_threshold'], config):
            updated_count += 1
        specials_updated_count += monitor_existing_specials(api, show, config)

    logging.info("Processing complete. Updated %s/%s shows", updated_count, len(shows))
    if config['monitor_existing_specials_enabled']:
        logging.info("Updated %s special episode(s) to monitored", specials_updated_count)

    return updated_count

def main():
    """Main execution flow"""
    args = parse_args()

    if args.version:
        print(f"Sonarr Tag Updater v{VERSION}")
        sys.exit(0)

    # Fail fast on bad configuration: without this the tool started up, logged a
    # generic "Starting" line, and only then died - or worse, ran the poll loop with
    # a bad interval. A clear message and a non-zero exit is what a container
    # supervisor needs to report the misconfiguration.
    try:
        config = get_config_from_env()
    except ValueError as e:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s')
        logging.error("Configuration error: %s", str(e))
        sys.exit(1)

    setup_logging(config['log_level'])
    logging.info("Starting Sonarr Tag Updater v%s", VERSION)

    api = SonarrAPI(config['sonarr_url'], config['sonarr_api_key'])
    interval_minutes = config['interval_minutes']

    while True:
        try:
            run_once(api, config, test_mode=args.test)

            logging.info("Next run in %s minutes", interval_minutes)
            time.sleep(interval_minutes * 60)

        except (RequestException, ValueError) as e:
            logging.error("Script failed: %s", str(e))
            logging.info("Retrying in 5 minutes")
            time.sleep(300)

def setup_logging(log_level):
    """Configure logging"""
    log_format = '%(asctime)s - %(levelname)s - %(message)s'

    # Clear any existing handlers
    logging.root.handlers = []

    # Set up console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[console_handler]
    )

    logging.info("Logging initialized at level: %s", log_level)
    logging.debug("Debug logging enabled")

if __name__ == "__main__":
    main()
