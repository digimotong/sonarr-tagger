# Pinned to the Debian suite this tag currently resolves to, and to 3.14 because
# tests.yml now runs the suite on it - the shipped interpreter and the tested one
# should not drift. The bare python:3.14-slim tag has already rolled a Debian
# suite once (bookworm -> trixie); pinning keeps the base from changing under a
# rebuild without a visible diff.
FROM python:3.14-slim-trixie

# docker logs is the only observability surface for this container, so stdout
# must never be buffered - otherwise logs appear in bursts or are lost on kill.
ENV PYTHONUNBUFFERED=1

# Install Python dependencies. --no-cache-dir keeps the pip wheel cache out of
# the image, and a single layer avoids shipping the intermediate state.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Install app
COPY sonarr-tagger /sonarr-tagger
WORKDIR /sonarr-tagger

# Drop privileges: the app only makes outbound HTTP calls and writes no files,
# so nothing here needs root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

# No HEALTHCHECK on purpose: there is no listening port to probe, and a check
# that only proves the process is alive would report "healthy" while Sonarr is
# unreachable. The INFO/ERROR logging is the real health signal.
CMD ["python", "main.py"]
