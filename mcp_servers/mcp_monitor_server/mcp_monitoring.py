import os
import httpx
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:8500")

mcp = FastMCP(
    "Lyra-Infrastructure-Monitor",
    dependencies=["httpx"],
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)
mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
mcp.settings.port = int(os.getenv("FASTMCP_PORT", os.getenv("MCP_PORT", "8301")))

@mcp.tool()
async def get_hardware_diagnostics(metric_type: str = "all") -> str:
    """
    Retrieves real-time infrastructure, GPU, and container telemetry from Prometheus.

    Args:
        metric_type: The category of metrics to retrieve. Options:
            - 'gpu': GPU temperature (DCGM_FI_DEV_GPU_TEMP) and compute utilization (DCGM_FI_DEV_GPU_UTIL) for dual RTX 5070 Ti GPUs.
            - 'vram': Video RAM framebuffer allocation, used memory (DCGM_FI_DEV_FB_USED), and free memory (DCGM_FI_DEV_FB_FREE).
            - 'system': Container CPU and RAM working set memory usage from cAdvisor across the stack.
            - 'all': Comprehensive diagnostic overview combining GPU, VRAM, and container metrics.
    """
    category = (metric_type or "all").lower().strip()
    if category not in ["gpu", "vram", "system", "all"]:
        return (
            f"Invalid metric_type '{metric_type}'. "
            "Supported parameters are 'gpu', 'vram', 'system', or 'all'."
        )

    lines: List[str] = [f"=== Prometheus Infrastructure Diagnostics [Category: {category.upper()}] ==="]

    async def _query(client: httpx.AsyncClient, expr: str) -> List[Dict[str, Any]]:
        try:
            resp = await client.get(
                f"{PROMETHEUS_URL.rstrip('/')}/api/v1/query",
                params={"query": expr},
                timeout=6.0
            )
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("status") == "success":
                    return payload.get("data", {}).get("result", [])
        except Exception as e:
            print(f"[Monitoring] Prometheus query failed for '{expr}': {e}", flush=True)
        return []

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Check Prometheus connectivity first
        try:
            health = await client.get(f"{PROMETHEUS_URL.rstrip('/')}/-/healthy", timeout=3.0)
            if health.status_code != 200:
                return f"Prometheus at {PROMETHEUS_URL} returned status {health.status_code}."
        except Exception as conn_err:
            return (
                f"Prometheus diagnostics unavailable: Unable to reach Prometheus server at "
                f"{PROMETHEUS_URL} ({conn_err})."
            )

        # ----------------------------------------------------------------------
        # 1. GPU COMPUTE & TEMPERATURE (DCGM)
        # ----------------------------------------------------------------------
        if category in ["gpu", "all"]:
            lines.append("\n[Dual RTX 5070 Ti Compute & Thermals (DCGM)]")
            temp_results = await _query(client, "DCGM_FI_DEV_GPU_TEMP")
            util_results = await _query(client, "DCGM_FI_DEV_GPU_UTIL")

            gpus: Dict[str, Dict[str, Any]] = {}
            for item in temp_results:
                metric = item.get("metric", {})
                gid = metric.get("gpu") or metric.get("device") or "0"
                model = metric.get("modelName") or "NVIDIA GeForce RTX 5070 Ti"
                val = item.get("value", [0, "0"])[1]
                try:
                    t_val = float(val)
                except ValueError:
                    t_val = 0.0
                gpus.setdefault(gid, {"model": model})["temp"] = t_val

            for item in util_results:
                metric = item.get("metric", {})
                gid = metric.get("gpu") or metric.get("device") or "0"
                model = metric.get("modelName") or "NVIDIA GeForce RTX 5070 Ti"
                val = item.get("value", [0, "0"])[1]
                try:
                    u_val = float(val)
                except ValueError:
                    u_val = 0.0
                gpus.setdefault(gid, {"model": model})["util"] = u_val

            if gpus:
                for gid in sorted(gpus.keys(), key=lambda x: int(x) if x.isdigit() else x):
                    info = gpus[gid]
                    model = info.get("model", "RTX 5070 Ti")
                    temp = info.get("temp", "N/A")
                    temp_str = f"{temp:.0f}°C" if isinstance(temp, (int, float)) else str(temp)
                    util = info.get("util", "N/A")
                    util_str = f"{util:.1f}%" if isinstance(util, (int, float)) else str(util)
                    lines.append(f"• GPU {gid} ({model}): Compute: {util_str} | Temp: {temp_str}")
            else:
                lines.append("• No DCGM compute metrics currently returned by Prometheus.")

        # ----------------------------------------------------------------------
        # 2. VRAM ALLOCATION & UTILIZATION (DCGM)
        # ----------------------------------------------------------------------
        if category in ["vram", "all"]:
            lines.append("\n[Dual RTX 5070 Ti Video RAM Allocation (DCGM)]")
            vram_used_res = await _query(client, "DCGM_FI_DEV_FB_USED")
            vram_free_res = await _query(client, "DCGM_FI_DEV_FB_FREE")

            vram_data: Dict[str, Dict[str, Any]] = {}
            for item in vram_used_res:
                metric = item.get("metric", {})
                gid = metric.get("gpu") or metric.get("device") or "0"
                model = metric.get("modelName") or "NVIDIA GeForce RTX 5070 Ti"
                val = item.get("value", [0, "0"])[1]
                try:
                    u_mb = float(val)
                except ValueError:
                    u_mb = 0.0
                vram_data.setdefault(gid, {"model": model})["used_mb"] = u_mb

            for item in vram_free_res:
                metric = item.get("metric", {})
                gid = metric.get("gpu") or metric.get("device") or "0"
                model = metric.get("modelName") or "NVIDIA GeForce RTX 5070 Ti"
                val = item.get("value", [0, "0"])[1]
                try:
                    f_mb = float(val)
                except ValueError:
                    f_mb = 0.0
                vram_data.setdefault(gid, {"model": model})["free_mb"] = f_mb

            if vram_data:
                for gid in sorted(vram_data.keys(), key=lambda x: int(x) if x.isdigit() else x):
                    v_info = vram_data[gid]
                    model = v_info.get("model", "RTX 5070 Ti")
                    used = v_info.get("used_mb", 0.0)
                    free = v_info.get("free_mb", 0.0)
                    total = used + free
                    pct = (used / total * 100.0) if total > 0 else 0.0
                    lines.append(
                        f"• GPU {gid} ({model}): Used: {used:,.0f} MB / {total:,.0f} MB ({pct:.1f}%) | Free: {free:,.0f} MB"
                    )
            else:
                lines.append("• No DCGM VRAM metrics currently returned by Prometheus.")

        # ----------------------------------------------------------------------
        # 3. CONTAINER CPU & RAM UTILIZATION (cAdvisor)
        # ----------------------------------------------------------------------
        if category in ["system", "all"]:
            lines.append("\n[Container Resource Breakdown (cAdvisor)]")
            # Query either name or container_name, falling back gracefully
            mem_query = 'topk(6, container_memory_working_set_bytes{name=~".+"})'
            cpu_query = 'topk(6, rate(container_cpu_usage_seconds_total{name=~".+"}[1m]) * 100)'

            mem_results = await _query(client, mem_query)
            if not mem_results:
                # Fallback to container_name label
                mem_query = 'topk(6, container_memory_working_set_bytes{container_name=~".+"})'
                mem_results = await _query(client, mem_query)

            cpu_results = await _query(client, cpu_query)
            if not cpu_results:
                cpu_query = 'topk(6, rate(container_cpu_usage_seconds_total{container_name=~".+"}[1m]) * 100)'
                cpu_results = await _query(client, cpu_query)

            def _extract_cname(metric: Dict[str, Any]) -> str:
                name = metric.get("name") or metric.get("container_name") or metric.get("container")
                if not name and metric.get("id"):
                    raw_id = metric.get("id", "")
                    name = raw_id.split("/")[-1][:12] if "/" in raw_id else raw_id[:12]
                return name or "unknown-container"

            containers: Dict[str, Dict[str, Any]] = {}
            for item in mem_results:
                metric = item.get("metric", {})
                cname = _extract_cname(metric)
                val = item.get("value", [0, "0"])[1]
                try:
                    bytes_val = float(val)
                except ValueError:
                    bytes_val = 0.0
                containers.setdefault(cname, {})["mem_bytes"] = bytes_val

            for item in cpu_results:
                metric = item.get("metric", {})
                cname = _extract_cname(metric)
                val = item.get("value", [0, "0"])[1]
                try:
                    cpu_pct = float(val)
                except ValueError:
                    cpu_pct = 0.0
                containers.setdefault(cname, {})["cpu_pct"] = cpu_pct

            if containers:
                for cname, stats in sorted(
                    containers.items(),
                    key=lambda kv: kv[1].get("mem_bytes", 0.0),
                    reverse=True
                ):
                    mem_b = stats.get("mem_bytes", 0.0)
                    mem_str = f"{mem_b / (1024 * 1024 * 1024):.2f} GB" if mem_b >= 1024 * 1024 * 1024 else f"{mem_b / (1024 * 1024):.1f} MB"
                    cpu_pct = stats.get("cpu_pct")
                    cpu_str = f"{cpu_pct:.1f}%" if cpu_pct is not None else "N/A"
                    lines.append(f"• Container '{cname}': RAM Working Set: {mem_str} | CPU (1m avg): {cpu_str}")
            else:
                lines.append("• No cAdvisor container metrics currently returned by Prometheus.")

    return "\n".join(lines)

if __name__ == "__main__":
    import os

    transport = os.getenv("MCP_TRANSPORT", "sse")
    
    if transport == "sse":
        mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("FASTMCP_PORT", "8301"))
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")