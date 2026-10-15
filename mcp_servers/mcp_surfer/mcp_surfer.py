import os
import httpx
from typing import Optional, List
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
try:
    from .scraper import fetch_webpage_content
except (ImportError, ValueError):
    try:
        from scraper import fetch_webpage_content
    except (ImportError, ValueError):
        from mcp_servers.mcp_surfer.scraper import fetch_webpage_content

SERPER_API_KEY = os.getenv("SERPER_API_KEY")

mcp = FastMCP(
    "Lyra-Web-Surfer",
    dependencies=["httpx", "beautifulsoup4"],
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)
mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
mcp.settings.port = int(os.getenv("FASTMCP_PORT", os.getenv("MCP_PORT", "8303")))

@mcp.tool()
async def search_google_serper(
    query: str,
    num_results: int = 5,
    search_type: str = "search"
) -> str:
    """
    Executes a real-time web search via Google Serper API to retrieve organic search results or news.

    Args:
        query: The search query string.
        num_results: Maximum number of search results to return (up to 10).
        search_type: Search mode: 'search' for general web results, or 'news' for current news articles.
    """
    api_key = os.getenv("SERPER_API_KEY", SERPER_API_KEY)
    if not api_key:
        return "Error: SERPER_API_KEY is not set."
    endpoint = "https://google.serper.dev/news" if search_type == "news" else "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": min(num_results, 10)}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("organic", [])
            lines = [f"=== Google Search Results for: '{query}' ===\n"]
            for i, res in enumerate(results, 1):
                lines.append(f"[{i}] {res.get('title')} ({res.get('link')})\n{res.get('snippet')}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

@mcp.tool()
async def fetch_webpage_text(url: str, max_chars: int = 6000) -> str:
    """
    Fetches a webpage and extracts clean, readable text stripped of boilerplate, scripts, and navigation.

    Args:
        url: The full HTTP/HTTPS URL of the target webpage to scrape.
        max_chars: Maximum character count of the cleaned text to return (default: 6000).
    """
    return await fetch_webpage_content(url, max_chars=max_chars)

if __name__ == "__main__":
    import os

    transport = os.getenv("MCP_TRANSPORT", "sse")
    
    if transport == "sse":
        mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("FASTMCP_PORT", "8300"))
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")