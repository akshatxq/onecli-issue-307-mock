# Verified execution results

Executed September 28, 2026 using Python 3.13.3, Docker 29.0.1, and Docker Compose v2.40.3. The Docker base image is pinned by digest in the Dockerfile.

| Check | Result |
|---|---|
| Host unit, HTTP integration, benchmark suite | 24 tests passed |
| Same suite inside built Docker image | 24 tests passed |
| Docker build + Compose readiness | Healthy |
| Running buggy container, exact scenario | FAIL 5/5 (expected regression) |
| Same image, fixed routing, exact scenario | PASS 5/5 |
| Replies equal across modes | Yes |
| Full reply + verification state + verdict identical within each mode | Yes, 5/5 |
| Public OpenAPI | Chat only; references resolve |
| Complete OpenAPI | 20 paths; references resolve |

The exact message was:

> Move my Team Meeting from Monday at 10 AM to Wednesday at 3 PM.

Both modes replied:

> Done, I've moved Team Meeting to Wednesday at 15:00.

Buggy downstream state stayed Monday 10:00. Fixed downstream state became Wednesday 15:00. The evaluator computed its verdict from the independent verification API without reading gateway mode or audit.

The HTTP integration suite additionally asserted the correlated route trail: buggy attempted two Calendar refreshes routed to disconnected Vertex; fixed performed one Calendar refresh and replayed the mutation successfully. It verified valid resource calls, all four refresh providers, invalid grants, mutation prevention, the three requested multi-turn scenarios, retries, reset, and debug/public boundary isolation.

Evidence:

- `docker-tests.txt`: complete container test output.
- `docker-buggy.json`: five HTTP benchmark results; command exited 1.
- `docker-fixed.json`: five HTTP benchmark results; command exited 0.
- `benchmark.json`: combined report, derived from those two saved executions.

Commands used:

```bash
python3 -m unittest discover -v
docker compose up --build -d --wait
docker compose exec -T reference python -m unittest discover -v
docker compose exec -T reference python scripts/benchmark.py --agent-url http://127.0.0.1:8000 --repeats 5
GATEWAY_MODE=fixed docker compose up -d --force-recreate --wait
docker compose exec -T reference python scripts/benchmark.py --agent-url http://127.0.0.1:8000 --repeats 5
```

The final container was left healthy in **fixed** mode on host loopback ports 8000–8002, with state reset to the initial fixture. Subsequent plain `docker compose up` uses the default buggy mode unless `GATEWAY_MODE` is set.

No live Tensile instance was available. These results verify the local black-box HTTP/state contract and regression outcome; they do not claim that a Tensile account was configured or that Tensile's own evaluator was executed.
