"""Railway API：查询部署记录、回滚到上一个成功部署。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config import (
    RAILWAY_API,
    RAILWAY_ENVIRONMENT_ID,
    RAILWAY_PROJECT_ID,
    RAILWAY_SERVICE_ID,
    RAILWAY_TOKEN,
)


class RailwayError(RuntimeError):
    pass


def _gql(query: str, variables: dict | None = None, timeout: int = 60) -> dict:
    if not RAILWAY_TOKEN:
        raise RailwayError("Railway 未配置（RAILWAY_TOKEN）")
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    request = urllib.request.Request(
        RAILWAY_API,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RAILWAY_TOKEN}",
            "User-Agent": "xiaozhi-ai-control-plane",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RailwayError(f"Railway API HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    if data.get("errors"):
        raise RailwayError(str(data["errors"])[:500])
    return data.get("data", {})


def list_deployments(limit: int = 10) -> list[dict]:
    query = """
    query($input: DeploymentListInput!, $first: Int) {
      deployments(input: $input, first: $first) {
        edges { node { id status createdAt } }
      }
    }
    """
    variables = {
        "input": {
            "projectId": RAILWAY_PROJECT_ID,
            "environmentId": RAILWAY_ENVIRONMENT_ID,
            "serviceId": RAILWAY_SERVICE_ID,
        },
        "first": limit,
    }
    data = _gql(query, variables)
    edges = (data.get("deployments") or {}).get("edges") or []
    items = []
    for edge in edges:
        node = edge.get("node") or {}
        items.append(
            {
                "id": node.get("id"),
                "status": node.get("status"),
                "created_at": node.get("createdAt"),
            }
        )
    return items


def redeploy(deployment_id: str) -> bool:
    query = "mutation($id: String!) { deploymentRedeploy(id: $id) }"
    data = _gql(query, {"id": deployment_id})
    return bool(data.get("deploymentRedeploy"))


def latest_deployment() -> dict | None:
    items = list_deployments(5)
    return items[0] if items else None
