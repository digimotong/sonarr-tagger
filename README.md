# Sonarr Tag Updater

Automatically updates show tags in Sonarr based on custom format scores, release groups, and quality information.

## Features

- **Score-based tagging**:
  - Uses the LOWEST score found across all episode files
  - `negative-score` when customFormatScore < 0
  - `positive-score` when customFormatScore > threshold (default: 100)
  - `no-score` when score is None or between 0-threshold

- **Quality tagging**:
  - `4k` when ANY episode file has 2160p resolution (configurable via TAG_4K env var)
 
- **Release group tagging**:
  - `mixed-release-groups` when ANY season (excluding Specials/season 0) has episodes with multiple different release groups (configurable via TAG_MIXED_RELEASE_GROUPS env var)
  - `motong` when ANY episode file has release group exactly "motong", compared case-insensitively (configurable via TAG_MOTONG env var)

- **Special episode monitoring**:
  - Automatically monitors existing specials (season 0) that have files on disk but are not monitored (configurable via MONITOR_EXISTING_SPECIALS env var)

## Containerized Deployment

The application is designed to run in Docker with Sonarr. Here's a sample compose configuration:

```yaml
services:
  sonarr-tagger:
    image: digimotong/sonarr-tagger:latest
    container_name: sonarr-tagger
    restart: unless-stopped
    depends_on:
      - sonarr
    environment:
      SONARR_URL: http://sonarr:8989    # Sonarr instance URL
      SONARR_API_KEY: your-api-key      # Sonarr API key (required)
      LOG_LEVEL: INFO                   # DEBUG, INFO, WARNING, ERROR, CRITICAL
      SCORE_THRESHOLD: 100              # Threshold for positive-score
      INTERVAL_MINUTES: 20              # Minutes between runs
      # TAG_4K: true                    # Enable 4k tagging
      # TAG_MIXED_RELEASE_GROUPS: true  # Enable mixed-release-groups tagging
      # TAG_MOTONG: true                # Enable motong tagging
      # MONITOR_EXISTING_SPECIALS: true # Enable monitoring of existing specials
```

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SONARR_URL` | Sonarr instance URL | `http://sonarr:8989` |
| `SONARR_API_KEY` | Sonarr API key with write permissions | `your-api-key` |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `SCORE_THRESHOLD` | `100` | Score threshold for positive-score tag |
| `INTERVAL_MINUTES` | `20` | Minutes between automatic runs (whole number, minimum 1) |
| `TAG_4K` | `false` | Enable 4k resolution tagging |
| `TAG_MIXED_RELEASE_GROUPS` | `false` | Enable mixed release groups tagging |
| `TAG_MOTONG` | `false` | Enable motong release group tagging |
| `MONITOR_EXISTING_SPECIALS` | `false` | Enable monitoring of existing specials (season 0) that have files on disk |

## Tag Management

The application automatically creates and manages these tags:

| Tag Name | Trigger Condition |
|----------|-------------------|
| negative-score | LOWEST episode score < 0 |
| positive-score | LOWEST episode score > threshold |
| no-score | No score or 0 ≤ score ≤ threshold |
| 4k | ANY episode file is 2160p (requires TAG_4K=true) |
| mixed-release-groups | ANY season (excluding Specials/season 0) has episodes with multiple different release groups (requires TAG_MIXED_RELEASE_GROUPS=true) |
| motong | ANY episode file has release group exactly "motong" (case-insensitive; requires TAG_MOTONG=true) |

Tags are created automatically if they don't exist in Sonarr.

## Monitoring

View container logs to monitor operation:

```bash
docker logs sonarr-tagger
```

Example log output:
```
2025-04-27 12:00:00 - INFO - Starting Sonarr Tag Updater v1.0.8
2025-04-27 12:00:02 - INFO - Processing 125 shows
2025-04-27 12:00:05 - DEBUG - Show: Breaking Bad - Score: 150 - Tag: positive-score
2025-04-27 12:00:05 - DEBUG - Added 4k tag for Breaking Bad
2025-04-27 12:00:10 - INFO - Processing complete. Updated 18/125 shows
2025-04-27 12:00:10 - INFO - Next run in 20 minutes
```

## Requirements

- Docker
- Sonarr v3+
- API key with write permissions
- Network access to Sonarr instance

## Command Line Flags

Both flags are intended for troubleshooting a container; normal operation needs
neither.

| Flag | Description |
|------|-------------|
| `--test` | Process only the first 5 shows, then carry on with the normal interval. Useful for verifying tags are applied as expected before waiting on a full pass. |
| `--version` | Print the version and exit. Reads no configuration, so it works without any environment variables set. |

```bash
docker run --rm digimotong/sonarr-tagger:latest python main.py --version
docker run --rm --env-file .env digimotong/sonarr-tagger:latest python main.py --test
```

## Behaviour and Failure Modes

**Configuration is validated at startup.** A missing or empty `SONARR_URL` or
`SONARR_API_KEY`, a `LOG_LEVEL` outside the accepted set, or an
`INTERVAL_MINUTES` that is not a whole number of at least
`1` aborts the container immediately with exit code `1` and a message such as:

```
2025-04-27 12:00:00 - ERROR - Configuration error: Missing required environment variables: SONARR_URL must be set
```

An `INTERVAL_MINUTES` of `0` or a negative value is rejected rather than accepted:
`time.sleep(0)` returns instantly, which would turn the poll loop into a busy loop
hammering the Sonarr API. The largest accepted value is `525600` (one year), since
anything beyond that effectively disables the updater.

**HTTP calls are bounded.** Every Sonarr request uses a 30 second timeout. A
stalled connection therefore fails the cycle instead of hanging indefinitely; the
failure is logged, and the next attempt happens after 5 minutes.

**Failures are retried, not fatal.** A Sonarr outage during a cycle logs
`Script failed: ...` and `Retrying in 5 minutes`. Only configuration errors stop
the process.

**Tags are managed, not just added.** On every pass, all tags owned by this tool
(`negative-score`, `positive-score`, `no-score`, and `motong`, `4k` and
`mixed-release-groups` when their feature flags are enabled) are removed from a
show and then re-applied to match the current state. Consequences worth knowing:

- Disabling a feature flag removes its tag from shows that already have it.
- Tags this tool does not own are left untouched.
- A show whose tags already match is not written to, so Sonarr's history is not
  polluted with no-op updates.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -v                      # full test suite
.venv/bin/python -m pytest --cov=main              # with coverage
.venv/bin/python -m pylint sonarr-tagger           # lint
```

The suite needs no network access and finishes in well under a second. It installs
an autouse guard that fails any test which attempts a real `time.sleep`, so a
regression in interval validation surfaces as an immediate test failure rather than
a hung CI job.
