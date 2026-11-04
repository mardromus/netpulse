FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"

# data and reports live in /data
VOLUME ["/data"]
WORKDIR /data
EXPOSE 8050

ENTRYPOINT ["netpulse"]
# live dashboard of the container's own network counters; override with e.g.
#   docker run -v $PWD:/data netpulse demo --out /data/out
CMD ["serve", "--live", "--host", "0.0.0.0", "--port", "8050"]
