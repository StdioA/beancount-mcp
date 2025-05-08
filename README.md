# Beancount MCP (Model Context Protocol) Server

A Beancount MCP server which can execute beancount query, and submit transaction to the ledger.

## Usage
`uvx beancount-mcp [--transport=stdio/sse] your_ledger.bean`

### Add to Claude

Add to `claude_desktop_config.json` (you can find this file by using Settings - Developer - Edit Config):

```
{
  "mcpServers": {
    "https://github.com/StdioA/beancount-mcp/tree/master": {
      "command": "uvx",
      "args": [
        "beancount-mcp",
        "--transport=stdio",
        "PATH_TO_YOUR_LEDGER"
      ],
      "disabled": false,
      "autoApprove": []
    }
  }
}

```
