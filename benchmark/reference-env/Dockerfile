# The governed reference environment: python:3.12-slim (Python 3.12.14,
# linux/amd64) plus git, which the test fixtures need to build real
# repositories. Normally provisioned with `apt-get install -y git`; container
# egress is unavailable, so the identical Debian git binary is copied from a
# locally built image that installed it from apt on the same base.
#
# Deliberately NOT copied: bubblewrap, and any global git configuration. Both
# are additions that image made, and both change what the suite observes.
FROM riftagent-dev:latest AS gitsrc
FROM python:3.12-slim
COPY --from=gitsrc /usr/bin/git /usr/bin/git
COPY --from=gitsrc /usr/lib/git-core /usr/lib/git-core
COPY --from=gitsrc /usr/share/git-core /usr/share/git-core
COPY --from=gitsrc /lib/x86_64-linux-gnu/libpcre2-8.so.0 /lib/x86_64-linux-gnu/libpcre2-8.so.0
# An offline package source, so `pip wheel` build isolation can resolve
# `setuptools>=68` without network. A config file rather than PIP_* env vars,
# which the packaging test deliberately strips.
RUN printf '[global]\nno-index = true\nfind-links = /wheels\n' > /etc/pip.conf

# Build:  docker build --network none -t rift-reference:3.12-slim .
# Gate:   docker run --rm --network none -v <repo>:/w -v <wheelhouse>:/wheels \
#           -w /w -e PYTHONPATH=/w/src:/w rift-reference:3.12-slim bash -c '
#           git config --global --add safe.directory "*"
#           python -m pip install -q -r requirements-dev.txt
#           python -m ruff check src tests && python -m ruff format --check src tests
#           python -m mypy src/riftagent && python -m pytest -q'
#
# With network available this image is unnecessary: plain python:3.12-slim plus
# `apt-get install -y git` is the same environment, and is what this reproduces.
