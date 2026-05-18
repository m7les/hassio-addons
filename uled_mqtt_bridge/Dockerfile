ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip

# Use a venv to avoid PEP 668 issues on Alpine.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY uled_bridge.py /app/uled_bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
