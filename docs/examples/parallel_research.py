"""Collect public research sources and optionally save them with Workspace MCP."""

import argparse
import asyncio
import json
import uuid
from urllib.parse import urlparse

from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport

from core.cli import DEFAULT_URL, _build_oauth

PARALLEL_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "workspace-mcp/parallel-research"
TIMEOUT = 60


def parallel_client() -> Client:
    """Use the anonymous endpoint without consulting API keys or OAuth storage."""
    return Client(
        StreamableHttpTransport(PARALLEL_URL, headers={"User-Agent": USER_AGENT}),
        timeout=TIMEOUT,
    )


def result_text(result: CallToolResult) -> str:
    """Keep source URLs, excerpts and any partial-result errors in the brief."""
    text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
    payload = result.structured_content
    if payload is None:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return text
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return text or json.dumps(payload, ensure_ascii=False)
    lines = []
    for source in payload["results"]:
        lines.extend([source.get("title") or "Source", source.get("url", "")])
        lines.extend(source.get("excerpts") or [])
        lines.append("")
    for key in ("errors", "warnings"):
        if payload.get(key):
            lines.append(f"{key}: {json.dumps(payload[key], ensure_ascii=False)}")
    return "\n".join(lines) or "No sources returned."


async def research(args: argparse.Namespace) -> str:
    """Execute MCP tools directly; do not generate a model-written summary."""
    session_id = str(uuid.uuid4())
    async with parallel_client() as client:
        tools = {tool.name for tool in await client.list_tools()}
        required = {"web_search", "web_fetch"} if args.fetch else {"web_search"}
        if not required <= tools:
            raise RuntimeError("Parallel MCP is missing a required search/fetch tool")
        search = await client.call_tool(
            "web_search",
            {
                "objective": args.objective,
                "search_queries": args.query,
                "session_id": session_id,
            },
        )
        sections = [
            "Research sources",
            args.objective,
            "Search results",
            result_text(search),
        ]
        if args.fetch:
            fetched = await client.call_tool(
                "web_fetch", {"urls": args.fetch, "session_id": session_id}
            )
            sections.extend(["Requested pages", result_text(fetched)])
    brief = "\n\n".join(sections)
    if args.save_doc:
        # Reuse workspace-cli's encrypted Google OAuth cache only for Workspace.
        async with Client(
            args.workspace_url, auth=_build_oauth(), timeout=TIMEOUT
        ) as workspace:
            created = await workspace.call_tool(
                "create_doc",
                {
                    "user_google_email": args.email,
                    "title": args.save_doc,
                    "content": brief,
                },
            )
            brief += "\n\n" + result_text(created)
    return brief


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objective", required=True)
    parser.add_argument(
        "--query", action="append", required=True, help="Repeat for related queries"
    )
    parser.add_argument(
        "--fetch", action="append", default=[], help="Fetch an explicitly chosen URL"
    )
    parser.add_argument(
        "--save-doc", metavar="TITLE", help="Create one Google Doc with the sources"
    )
    parser.add_argument("--email", help="Google account for --save-doc")
    parser.add_argument("--workspace-url", default=DEFAULT_URL)
    args = parser.parse_args(argv)
    if bool(args.save_doc) != bool(args.email):
        parser.error("--save-doc and --email must be supplied together")
    if not args.objective.strip() or any(not query.strip() for query in args.query):
        parser.error("objective and queries must not be empty")
    if len(args.fetch) > 20:
        parser.error("--fetch supports at most 20 URLs per run")
    for url in args.fetch:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            parser.error("--fetch requires an HTTP or HTTPS URL")
    return args


def main() -> None:
    args = parse_args()
    print(asyncio.run(research(args)))


if __name__ == "__main__":
    main()
