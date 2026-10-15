FROM python:3.11-slim AS base
WORKDIR /app

# 1. Explicitly add /app to Python's module lookup path
ENV PYTHONPATH=/app

# Install system dependencies required for LanceDB/PyArrow compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python requirements from root ai-stack
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy decoupled modules into the container image
COPY mcp_servers/ /app/mcp_servers/

# --- TARGET 1: Primary Memory Engine REST API ---
FROM base AS engine
EXPOSE 8000
CMD ["uvicorn", "mcp_servers.mcp_memory_engine.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- TARGET 2: Long Term Memory MCP Server ---
FROM base AS mcp-memory-target
EXPOSE 8300
CMD ["python", "-m", "mcp_servers.mcp_memory_engine.mcp_memoryengine"]

# --- TARGET 3: Infrastructure Monitoring MCP Server ---
FROM base AS mcp-monitor-target
EXPOSE 8301
CMD ["python", "-m", "mcp_servers.mcp_monitor_server.mcp_monitoring"]

# --- TARGET 4: Agency Tools & Memory Spotlight MCP Server ---
FROM base AS mcp-agency-target
EXPOSE 8302
ENV MCP_TRANSPORT=sse
CMD ["python", "-m", "mcp_servers.mcp_agency_tools.mcp_agency"]

# --- TARGET 5: Autonomous Web Surfer MCP Server ---
FROM base AS mcp-surfer-target
EXPOSE 8303
ENV MCP_TRANSPORT=sse
CMD ["python", "-m", "mcp_servers.mcp_surfer.mcp_surfer"]

# --- TARGET 6: Workspace & Fileserver MCP Server ---
FROM base AS mcp-fileserver-target
EXPOSE 8304
ENV MCP_TRANSPORT=sse
CMD ["python", "-m", "mcp_servers.mcp_fileserver.workspace_server"]
