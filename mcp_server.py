"""Standard MCP stdio adapter; model/index lifetime belongs to the shared service."""
from mcp.server.fastmcp import FastMCP
from service import start, request
from paths import source_root


def run(root):
    root = source_root(root)
    start(root)
    mcp = FastMCP('Code Search Lab')

    @mcp.tool()
    def search_code(query: str, mode: str='auto', limit: int=5, context: dict | None=None,
                    path: str | None=None, sync_paths: list[str] | None=None, full_sync: bool=False) -> dict:
        """Search saved source. Context accepts active_file, selection and client diagnostics."""
        return request(root,'query',{'query':query,'mode':mode,'limit':limit,'context':context,
                       'path':path,'sync_paths':sync_paths or [],'full_sync':full_sync},timeout=1800)

    @mcp.tool()
    def navigate_code(symbol: str='', mode: str='definitions', file: str | None=None,
                      line: int | None=None, column: int=0, limit: int=20) -> dict:
        """Definitions, references, imports, calls or callers. Lines 1-based; columns UTF-16, 0-based."""
        if mode not in {'definitions','references','imports','calls','callers'}: raise ValueError('Invalid navigation mode.')
        return request(root,'query',{'query':symbol,'mode':mode,'file':file,'line':line,'column':column,'limit':limit})

    @mcp.tool()
    def read_source(file: str, start: int=1, end: int | None=None) -> dict:
        """Read at most 200 lines from an indexed source file; no edits or commands."""
        return request(root,'read',{'file':file,'start':start,'end':end,'sync_paths':[file]})

    @mcp.tool()
    def index_status() -> dict:
        """Inspect index generation, model lifetime and last indexing error."""
        return request(root,'status')

    mcp.run(transport='stdio')
