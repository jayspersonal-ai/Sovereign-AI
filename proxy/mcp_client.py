import os
import json
from typing import List, Dict, Any
from mcp import ClientSession
from mcp.client.sse import sse_client

def get_default_mcp_servers() -> Dict[str, str]:
    in_docker = os.getenv("RUNNING_IN_DOCKER") == "true"
    return {
        "memory": os.getenv("MCP_MEMORY_URL", f"http://{'mcp-memory:8300' if in_docker else 'localhost:8300'}/sse"),
        "monitor": os.getenv("MCP_MONITOR_URL", f"http://{'mcp-monitor:8301' if in_docker else 'localhost:8301'}/sse"),
        "agency": os.getenv("MCP_AGENCY_URL", f"http://{'mcp-agency:8302' if in_docker else 'localhost:8302'}/sse"),
        "surfer": os.getenv("MCP_SURFER_URL", f"http://{'mcp-surfer:8303' if in_docker else 'localhost:8303'}/sse"),
        "files": os.getenv("MCP_FILESERVER_URL", f"http://{'mcp-fileserver:8304' if in_docker else 'localhost:8304'}/sse"),
    }

MCP_SERVERS = get_default_mcp_servers()

class FastMCPGateway:
    def __init__(self, server_map: Dict[str, str] = MCP_SERVERS):
        self.server_map = server_map
        self._cached_tools: List[Dict[str, Any]] = []

    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """Queries running FastMCP SSE servers and builds standard OpenAI tool schemas."""
        tools = []
        for s_name, s_url in self.server_map.items():
            discovered_tools = []
            try:
                async with sse_client(s_url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        for tool in result.tools:
                            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
                            if schema is None and hasattr(tool, "model_dump"):
                                schema = tool.model_dump().get("inputSchema") or tool.model_dump().get("input_schema")
                            discovered_tools.append({
                                "type": "function",
                                "function": {
                                    "name": f"{s_name}__{tool.name}",
                                    "description": tool.description or "",
                                    "parameters": schema or {}
                                }
                            })
                        print(f"[MCP-Gateway] Connected to '{s_name}' ({s_url}): found {len(discovered_tools)} tools", flush=True)
            except Exception as e:
                if hasattr(e, "exceptions"):
                    err_detail = "; ".join([f"{type(sub).__name__}: {sub}" for sub in e.exceptions])
                else:
                    err_detail = f"{type(e).__name__}: {e}"
                if discovered_tools:
                    print(f"[MCP-Gateway] '{s_name}' stream closed after tool discovery: {err_detail}", flush=True)
                else:
                    print(f"[MCP-Gateway] Failed connecting to '{s_name}' at {s_url}: {err_detail}", flush=True)
            if discovered_tools:
                tools.extend(discovered_tools)
        if tools:
            self._cached_tools = tools
        return self._cached_tools

    async def call_tool(self, full_function_name: str, arguments: dict) -> str:
        """Executes a tool call on the target FastMCP server."""
        if "__" not in full_function_name:
            for s_name in self.server_map.keys():
                candidate = f"{s_name}__{full_function_name}"
                if any(t["function"]["name"] == candidate for t in self._cached_tools):
                    full_function_name = candidate
                    break
            else:
                return f"Error: Tool name must be in format 'server__toolname', got {full_function_name}"

        server_name, tool_name = full_function_name.split("__", 1)
        server_url = self.server_map.get(server_name)
        if not server_url:
            return f"Error: Unknown MCP server '{server_name}'"

        try:
            async with sse_client(server_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    outputs = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            outputs.append(content.text)
                    return "\n".join(outputs) if outputs else "Tool executed successfully."
        except Exception as e:
            return f"Error executing {tool_name} on {server_name}: {str(e)}"

mcp_gateway = FastMCPGateway()
