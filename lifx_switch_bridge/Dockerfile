ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python (bashio is already in the HA base image).
RUN apk add --no-cache python3 py3-pip

# Use a venv to avoid PEP 668 issues on Alpine.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY run.sh /
COPY lifx_switch_bridge.py /

RUN chmod a+x /run.sh

CMD ["/run.sh"]
