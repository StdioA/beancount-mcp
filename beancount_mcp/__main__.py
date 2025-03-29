"""Command line interface for Beancount MCP Server."""
import argparse


def main():
    """Main entry point for the Beancount MCP Server."""
    parser = argparse.ArgumentParser(description="Beancount Model Context Protocol Server")
    parser.add_argument(
        "beancount_file",
        type=str,
        help="Path to the Beancount ledger file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind the server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to (default: 8000)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="sse",
        help="Transport to use for communication (default: sse)",
    )

    args = parser.parse_args()

    # 检查Beancount文件是否存在
    from beancount_mcp.server import mcp, init_manager
    init_manager(args.beancount_file)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
