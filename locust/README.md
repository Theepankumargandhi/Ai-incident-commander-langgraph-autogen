# Locust Load Testing

This load test exercises the main incident workflow with weighted traffic:

| Endpoint | Weight |
| --- | ---: |
| `POST /incidents` | 20% |
| `POST /incidents/{id}/process` | 30% |
| `GET /incidents` | 30% |
| `GET /runs/{id}/cost` | 20% |

Users wait 1 to 3 seconds between requests. If `LOCUST_BEARER_TOKEN` or `API_TOKEN` is set, Locust sends it as `Authorization: Bearer <token>`.

## Run Locally

Start the backend first:

```bash
uvicorn app.main:app --reload
```

Then start the Locust UI:

```bash
locust -f locust/locustfile.py
```

Open `http://localhost:8089`, set the host to `http://127.0.0.1:8000`, and choose the user count and spawn rate.

## Run Headless

```bash
locust -f locust/locustfile.py --host http://127.0.0.1:8000 --headless -u 10 -r 2 --run-time 60s
```

For protected APIs:

```bash
LOCUST_BEARER_TOKEN=your-token locust -f locust/locustfile.py --host http://127.0.0.1:8000 --headless -u 10 -r 2 --run-time 60s
```

## Reading Results

The most useful Locust fields are:

| Field | Meaning |
| --- | --- |
| Requests/s | Throughput the backend sustained during the run. |
| Failures | Count and percentage of requests that returned unexpected responses. |
| Median / p50 | Typical response time. Half of requests were faster than this. |
| 95% / p95 | Tail latency. 95% of requests were faster than this. |
| Max | Slowest response observed during the run. |

CI enforces `LOCUST_MAX_ERROR_RATE=0.05` and `LOCUST_MAX_P95_MS=2000`, so the load test fails when the error rate exceeds 5% or p95 latency exceeds 2000ms.
