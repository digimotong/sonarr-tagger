# Pinned to the Debian suite this tag resolves to, and to 3.14 because tests.yml
# runs the suite on it: the shipped interpreter and the tested one must not drift.
FROM python:3.14-slim-trixie

# docker logs is the only observability surface, so stdout must stay unbuffered.
ENV PYTHONUNBUFFERED=1

# --no-cache-dir keeps the pip wheel cache out of the image; one layer avoids
# shipping the intermediate state.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY sonarr-tagger /sonarr-tagger
WORKDIR /sonarr-tagger

# Drop privileges: the app only makes outbound HTTP calls and writes no files.
RUN useradd --create-home --uid 1000 appuser
USER appuser

# No HEALTHCHECK: there is no listening port, and a liveness probe would report
# "healthy" while Sonarr is unreachable. INFO/ERROR logging is the health signal.
CMD ["python", "main.py"]
