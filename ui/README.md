# PromoFinder dashboard

From the project directory:

```powershell
.tools/uv/uv.exe --cache-dir .tools/cache pip install --python .venv/Scripts/python.exe -r ui/requirements.txt
.venv/Scripts/python.exe -m streamlit run ui/app.py
```

## Deploy with Docker Compose

On the server, clone this repository and run:

```shell
docker compose up -d --build
```

The app listens on container port `8501`, is configured for the `/kuku` base path,
and is published on host port `8502` by default. Change the published port without
editing the Compose file by setting `KUKU_PORT`, for example:

```shell
KUKU_PORT=8510 docker compose up -d --build
```

The `kuku_jobs`, `kuku_uploads`, and `kuku_runs` volumes persist dashboard jobs,
uploaded scripts, and run/cache data across container replacements. The repository's
`data/` directory is mounted read-only. The app container publishes the host port
on all interfaces so a separate Nginx container can reach it; restrict access to
that port with the server firewall so only the proxy can connect.

Configure the other Compose stack's Nginx to proxy to the Docker host's IP and
published port. Keep the `/kuku` prefix in the upstream request (do not strip it),
and forward WebSocket upgrade headers:

```nginx
location = /kuku {
    return 301 /kuku/;
}

location /kuku/ {
    proxy_pass http://<docker-host-ip>:8502;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
}
```

Replace `<docker-host-ip>` with the server address reachable from the Nginx
container. If `KUKU_PORT` is changed, update the Nginx upstream port to match.

Choose episodes in the modal, set a candidate cap and pacing, then run. Parse-only mode makes no model calls. Existing saved runs are immediately inspectable. Dashboard runs read credentials only from the Agent settings panel. Choose Haiku recommended, Free-tier, or Mixed, then set each agent's provider and model ID. Enter shared provider keys or disable the shared-key option for individual keys per agent. Keys are kept in session/worker memory and passed through a private standard-input pipe; they are not written to run settings, caches, logs, or environment files. The original CLI remains independent. Closing/reloading a browser session may require re-entering keys. Settings are snapshotted when a run starts; changes affect future runs only. The key check calls the provider's model-list endpoint without generating text; it does not confirm balance or model inference access. Enable the Haiku batch checkbox on Episode analyst and/or Opportunity discovery to use Anthropic Message Batches (50% token discount, asynchronous processing up to 24 hours). Other agents use standard Messages API billing. Independent episode requests are grouped by agent; series analysis remains a dependency between the two batches. Cross-episode discovery, if enabled in the pipeline, uses a separate batch when discovery batch mode is on.

Workers run separately from Streamlit and save input snapshots, progress, stage requests, and logs under `ui/jobs`. Pause takes effect before the next model request. Resume releases that pause. Restarting a failed run reuses valid cached calls; it is not a durable job restart. A browser reload can lose the worker-control session, but saved results remain discoverable. Only one worker per browser session is allowed; this local interview app is not a multi-user service.

All episode rankings and full extracted scripts are shown, including rejected and pending candidates. Missing scores are not zero scores. Exact successful LLM messages are retrieved by matching cached outputs; deterministic builder inputs are reconstructed from saved artifacts. Historical missing requests cannot be recovered. Stage buttons provide inspection, not arbitrary stage execution. The existing pipeline still controls ordering and retry policy.

Batch IDs, non-secret request bodies, status counts, and results are stored under `ui/jobs/<job>/batches`. Use **Reconnect / retry saved job** with the same provider account keys after a worker interruption. Saved inputs and model settings are reused; completed responses are cached. A worker lock prevents two processes from running the same job. Pause does not cancel batches already submitted to Anthropic. Failed/expired/canceled batch requests are reported without automatic paid resubmission or standard-API fallback. If submission acknowledgement is lost, processing stops to avoid duplicate charges: reconcile the saved manifest with the batch ID from Anthropic Console before reconnecting. This rare reconciliation is manual. Batch results are matched by custom ID, not response order.
