# Public research sources in Google Docs

Collect web sources with free [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp),
then optionally save the excerpts and source URLs in a Google Doc through Workspace MCP.
Anonymous search uses Fast mode and needs no Parallel account or API key. The free
endpoint has lower rate limits and caps excerpts at roughly 25,000 characters per call.

This is a standalone FastMCP client example. It adds no server tools and does not
change Google Custom Search or your Workspace configuration. It collects excerpts,
not a generated summary, and uses Streamable HTTP for both connections.

From a checkout of this repository, install the existing dependencies:

```bash
uv sync --frozen
```

Search and print the sources without any Google credentials or Workspace server:

```bash
uv run --frozen python docs/examples/parallel_research.py \
  --objective "Find Google Docs API guidance for inserting document text" \
  --query "Google Docs API insertText"
```

Repeat `--query` for related searches. Add `--fetch https://example.com/page` when
you want excerpts from a specific page (up to 20 URLs); the example does not
automatically fetch every search result.

To save the collected sources, first start Workspace MCP over HTTP with Docs
enabled, following the [OAuth setup](https://workspacemcp.com/quick-start).
The example reuses `workspace-cli`'s encrypted OAuth cache and opens the browser
for authorization if needed. Supply your Google account and a document title:

```bash
uv run --frozen python docs/examples/parallel_research.py \
  --objective "Find Google Docs API guidance for inserting document text" \
  --query "Google Docs API insertText" \
  --fetch https://developers.google.com/workspace/docs/api/how-tos/move-text \
  --save-doc "Docs API research sources" --email you@example.com
```

Workspace defaults to `http://localhost:8000/mcp`; use `--workspace-url` for your
own deployment. `--save-doc` creates a new document with plain text, including the
excerpts and URLs, and prints its link. Only that explicit option contacts Workspace.
Review the sources before sharing them. Each run creates a new document; do not
rerun a failed save without first checking whether the document was created.
