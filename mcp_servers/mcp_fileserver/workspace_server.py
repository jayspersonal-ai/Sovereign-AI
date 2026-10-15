import os
from mcp.server.fastmcp import FastMCP

# Import the logic functions from this server's own tools.py file
from mcp_servers.mcp_fileserver.tools import (
    manage_workspace_files,
    manage_workspace_images
)

# Explicitly bind to 0.0.0.0 to allow cross-container Docker networking
port = int(os.getenv("FASTMCP_PORT", 8304))
mcp = FastMCP("Lyra Workspace Server", host="0.0.0.0", port=port)

# Register the tool functions directly with FastMCP decorators.
# Two tools: one for files, one for images.
mcp.tool()(manage_workspace_files)
mcp.tool()(manage_workspace_images)

if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "sse"))
