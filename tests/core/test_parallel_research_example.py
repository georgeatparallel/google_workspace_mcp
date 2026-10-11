"""Exercise the runnable example through FastMCP tool dispatch, without live APIs."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import httpx2
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from gdocs.docs_tools import create_doc

EXAMPLE_PATH = Path(__file__).parents[2] / "docs/examples/parallel_research.py"
spec = importlib.util.spec_from_file_location("parallel_research_example", EXAMPLE_PATH)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


@pytest.mark.asyncio
async def test_search_fetch_and_existing_docs_tool(monkeypatch):
    parallel = FastMCP("Parallel fixture")
    calls = []

    @parallel.tool
    def web_search(objective: str, search_queries: list[str], session_id: str) -> dict:
        calls.append(("search", session_id, objective, search_queries))
        return {
            "results": [
                {
                    "title": "Docs guide",
                    "url": "https://example.com/docs",
                    "excerpts": ["Insert text at index 1."],
                }
            ]
        }

    @parallel.tool
    def web_fetch(urls: list[str], session_id: str) -> dict:
        calls.append(("fetch", session_id, urls))
        return {
            "results": [
                {
                    "title": "Requested page",
                    "url": urls[0],
                    "excerpts": ["Fetched excerpt."],
                }
            ],
            "errors": [
                {"url": "https://example.com/missing", "message": "Unavailable"}
            ],
        }

    workspace = FastMCP("Workspace fixture")
    docs_service = Mock()
    docs_service.documents().create().execute.return_value = {
        "documentId": "research-doc"
    }
    docs_service.documents().batchUpdate().execute.return_value = {}
    docs_service.reset_mock()

    @workspace.tool(name="create_doc")
    async def create(user_google_email: str, title: str, content: str) -> str:
        # Execute the existing Workspace implementation behind its auth wrappers.
        return await create_doc.__wrapped__.__wrapped__(
            service=docs_service,
            user_google_email=user_google_email,
            title=title,
            content=content,
        )

    monkeypatch.setattr(example, "parallel_client", lambda: Client(parallel))
    monkeypatch.setattr(example, "_build_oauth", lambda: "workspace-auth-only")

    def workspace_client(url, *, auth, timeout):
        assert url == "http://localhost:8000/mcp"
        assert auth == "workspace-auth-only"
        assert timeout == 60
        return Client(workspace)

    monkeypatch.setattr(example, "Client", workspace_client)
    args = example.parse_args(
        [
            "--objective",
            "Docs research",
            "--query",
            "Docs API text",
            "--fetch",
            "https://example.com/docs",
            "--save-doc",
            "Research",
            "--email",
            "user@example.com",
        ]
    )
    brief = await example.research(args)
    assert calls[0][1] == calls[1][1]
    assert calls[0][2:] == ("Docs research", ["Docs API text"])
    assert calls[1][2] == ["https://example.com/docs"]
    assert "https://example.com/docs" in brief
    assert "Insert text at index 1." in brief
    assert "Fetched excerpt." in brief
    assert "Unavailable" in brief
    assert "https://docs.google.com/document/d/research-doc/edit" in brief
    docs_service.documents().create.assert_called_once_with(body={"title": "Research"})
    inserted = docs_service.documents().batchUpdate.call_args.kwargs["body"][
        "requests"
    ][0]["insertText"]["text"]
    assert inserted == brief.split("\n\nCreated Google Doc")[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_anonymous_path_and_tool_errors_never_open_workspace(monkeypatch, fail):
    parallel = FastMCP("Search fixture")

    @parallel.tool
    def web_search(objective: str, search_queries: list[str], session_id: str) -> str:
        if fail:
            raise ValueError("Search unavailable")
        return "Useful sources"

    monkeypatch.setattr(example, "parallel_client", lambda: Client(parallel))

    def forbidden(*args, **kwargs):
        pytest.fail("Anonymous research must not load OAuth storage or open Workspace")

    monkeypatch.setattr(example, "_build_oauth", forbidden)
    monkeypatch.setattr(example, "Client", forbidden)
    args = example.parse_args(["--objective", "Research", "--query", "Docs API text"])
    if fail:
        args.save_doc = "Should not be created"
        args.email = "user@example.com"
        with pytest.raises(ToolError):
            await example.research(args)
    else:
        assert "Useful sources" in await example.research(args)


@pytest.mark.parametrize(
    "extra",
    [
        ["--email", "user@example.com"],
        ["--save-doc", "Title"],
        ["--fetch", "file:///tmp/page"],
        ["--fetch", "https://example.com"] * 21,
    ],
)
def test_invalid_arguments_fail_before_network(extra):
    with pytest.raises(SystemExit):
        example.parse_args(
            ["--objective", "Research", "--query", "Docs API text", *extra]
        )


@pytest.mark.asyncio
async def test_http_requests_use_anonymous_endpoint_and_user_agent(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "must-not-be-used")
    requests = []

    def respond(request):
        requests.append(request)
        if request.method != "POST":
            return httpx2.Response(204)
        rpc = json.loads(request.content)
        if "id" not in rpc:
            return httpx2.Response(202)
        if rpc["method"] == "server/discover":
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                },
            )
        if rpc["method"] == "initialize":
            result = {
                "protocolVersion": rpc["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif rpc["method"] == "tools/list":
            result = {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object"}}
                    for name in ("web_search", "web_fetch")
                ]
            }
        elif rpc["method"] == "tools/call":
            result = {
                "content": [
                    {"type": "text", "text": "Useful source https://example.com"}
                ]
            }
        else:
            raise AssertionError(rpc)
        return httpx2.Response(
            200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result}
        )

    original_transport = example.StreamableHttpTransport

    def transport(url, *, headers):
        return original_transport(
            url,
            headers=headers,
            httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(
                transport=httpx2.MockTransport(respond), **kwargs
            ),
        )

    monkeypatch.setattr(example, "StreamableHttpTransport", transport)
    args = example.parse_args(
        [
            "--objective",
            "Research",
            "--query",
            "Docs API text",
            "--fetch",
            "https://example.com",
        ]
    )
    assert "Useful source" in await example.research(args)
    for request in requests:
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["user-agent"] == "workspace-mcp/parallel-research"
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
    methods = [
        json.loads(request.content).get("method")
        for request in requests
        if request.method == "POST"
    ]
    assert "initialize" in methods
    assert "tools/list" in methods
    assert methods.count("tools/call") == 2
