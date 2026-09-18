# Sonarr Tag Updater

Automatically updates show tags in Sonarr based on custom format scores, release groups, and quality information.

## Features

- **Score-based tagging**:
  - Uses the LOWEST score found across all episode files
  - `negative-score` when customFormatScore < 0
  - `positive-score` when customFormatScore > threshold (default: 100)
  - `no-score` when score is None or between 0-threshold

- **Quality tagging**:
  - `4k` when ANY episode file has 2160p resolution

- **Release group tagging**:
  - `mixed-release-groups` when ANY season (excluding Specials/season 0) has episodes with multiple different release groups
  - `motong` when ANY episode file has release group "motong"

- **Special episode monitoring**:
  - Monitors existing specials (season 0) that have files on disk but are not monitored

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
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
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
| motong | ANY episode file has release group "motong" (requires TAG_MOTONG=true) |
| _none_ | Other tags are left untouched; a show whose tags already match is not written to |

Tags are created automatically if they don't exist in Sonarr. Disabling a feature
flag removes its tag from shows that already have it.

## Monitoring

View container logs to monitor operation:

```bash
docker logs sonarr-tagger
```

Example log output (with `LOG_LEVEL: DEBUG`):
```
2025-04-27 12:00:00,000 - INFO - Starting Sonarr Tag Updater v1.0.11
2025-04-27 12:00:02,300 - INFO - Processing 125 shows
2025-04-27 12:00:05,400 - DEBUG - Show: Breaking Bad - Score: 150 - Tag: positive-score
2025-04-27 12:00:10,500 - INFO - Processing complete. Updated 18/125 shows
2025-04-27 12:00:10,501 - INFO - Next run in 20 minutes
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--test` | Process only the first 5 shows, then continue the normal loop. Handy for a first run. |
| `--version` | Print the version and exit without reading any configuration. |

```bash
docker run --rm digimotong/sonarr-tagger:latest python main.py --version
docker run --rm --env-file .env digimotong/sonarr-tagger:latest python main.py --test
```

## Troubleshooting

- Configuration is validated at startup. A missing or invalid value exits with
  status `1` and a single `Configuration error: ...` line instead of a traceback;
  fix the environment and recreate the container.
- Every Sonarr request uses a 30 second timeout. A failed cycle is logged and
  retried after 5 minutes rather than stopping the container.
- A rejected API key (`401`/`403`) is **not** retried: the container exits with
  status `1` and an `Authentication failed: ...` line, so the restart policy
  surfaces the bad key. Retrying can never fix a wrong key.
- Tags are re-read immediately before every write, so tag edits made in the
  Sonarr UI while a pass is running are not reverted.

## Development

Requires Python 3.12+. The application code lives in `sonarr-tagger/`.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -q          # test suite
.venv/bin/python -m pylint sonarr-tagger
```

### Twin-divergence check

`tests/test_twin_parity.py` asserts that this repository and `radarr-tagger`
still agree on the logic and docs that must stay in lockstep. It needs both
checkouts side by side and **skips** otherwise; CI runs it in a dedicated
`parity` job. When it fails, port the change to the sibling rather than
relaxing the check.

## Requirements

- Docker
- Sonarr v3+
- API key with write permissions
- Network access to Sonarr instance
