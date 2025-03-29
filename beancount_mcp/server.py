"""Beancount MCP Server implementation."""

import os
import re
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

from beancount import loader
from beanquery.query import run_query
from beancount.core import data, getters
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from mcp.server.fastmcp import FastMCP


class BeancountFileHandler(FileSystemEventHandler):
    """File system event handler for Beancount files."""

    def __init__(self, server: "BeancountMCPServer"):
        """Initialize the handler with a reference to the server.

        Args:
            server: The BeancountFileHandler instance.
        """
        self.server = server
        self.last_modified = time.time()
        self.cooldown = 2

    def on_modified(self, event):
        """Handle file modification events.

        Args:
            event: The file system event.
        """
        if event.is_directory:
            return

        if not str(event.src_path).endswith(".bean"):
            return

        current_time = time.time()
        if current_time - self.last_modified < self.cooldown:
            return

        self.last_modified = current_time
        logging.info(f"Detected changes in {event.src_path}, reloading...")
        try:
            self.server.load_beancount_file()
        except Exception as e:
            logging.error(f"Error reloading Beancount file: {e}")


class BeancountMCPServer:
    """Beancount Model Context Protocol Server implementation."""

    def __init__(self, beancount_file: str):
        """Initialize the server with a Beancount file.

        Args:
            beancount_file: Path to the Beancount ledger file.
        """
        self.entry_path = beancount_file
        self.beancount_file = Path(beancount_file).resolve()
        self.load_beancount_file()
        self.setup_file_watcher()

    def load_beancount_file(self):
        """Load the Beancount file and extract necessary data."""
        try:
            self.entries, self.errors, self.options_map = loader.load_file(str(self.beancount_file))
            self.accounts = getters.get_accounts(self.entries)
            self.last_load_time = time.time()
            if self.errors:
                logging.error(f"Found {len(self.errors)} errors in the Beancount file")
        except Exception as e:
            logging.error(f"Error loading Beancount file: {e}")
            raise

    def setup_file_watcher(self):
        """Setup a file watcher to monitor changes to the Beancount file."""
        self.observer = Observer()
        event_handler = BeancountFileHandler(self)
        self.observer.schedule(event_handler, str(self.beancount_file.parent), recursive=True)
        self.observer.start()
        logging.info(f"Started file watcher for {self.beancount_file.parent}")

    def resources(self) -> List[str]:
        """Handle model/resources request.

        Returns:
            Available resources.
        """
        # List all files in the Beancount directory
        ledger_dir = self.beancount_file.parent
        ledger_files = []
        for path in ledger_dir.glob("**/*.bean"):
            if path.is_file():
                ledger_files.append(str(path.relative_to(ledger_dir)))

        return ledger_files

    def query_bql(self, query_string: str) -> Dict[str, Any]:
        """Execute a BQL query.

        Args:
            params: The parameters for the query.

        Returns:
            The query results.
        """
        if not query_string:
            raise ValueError("Query parameter is required")

        pattern = re.compile(r'[\'"](\d{4}-\d{2}-\d{2})[\'"]')
        query_string = re.sub(pattern, r'\1', query_string)
        from_pattern = re.compile(r'FROM transactions?')
        query_string = re.sub(from_pattern, '', query_string)
        try:
            types, rows = run_query(self.entries, self.options_map, query_string)
            column_names = [
                {
                    "name": t.name,
                    "type": f'{t.datatype.__module__}.{t.datatype.__qualname__}',
                }
                for t in types
            ]

            return {
                "columns": column_names,
                "rows": [[str(c) for c in r] for r in rows[:200]],
            }
        except Exception as e:
            raise ValueError(f"BQL query error: {str(e)}")

    def tool_get_transaction(self, tx_id: str) -> Dict[str, Any]:
        """Get transaction details by ID.

        Args:
            params: The parameters containing the transaction ID.

        Returns:
            The transaction details and file location.
        """
        if not tx_id:
            raise ValueError("Transaction ID is required")

        for entry in self.entries:
            if isinstance(entry, data.Transaction) and entry.meta.get("id") == tx_id:
                filename = entry.meta.get("filename")
                lineno = entry.meta.get("lineno")

                tx_dict = {
                    "date": str(entry.date),
                    "flag": entry.flag,
                    "payee": entry.payee,
                    "narration": entry.narration,
                    "tags": list(entry.tags) if entry.tags else [],
                    "links": list(entry.links) if entry.links else [],
                    "postings": []
                }

                for posting in entry.postings:
                    posting_dict = {
                        "account": posting.account,
                        "units": str(posting.units) if posting.units else None,
                        "cost": str(posting.cost) if posting.cost else None,
                        "price": str(posting.price) if posting.price else None,
                    }
                    tx_dict["postings"].append(posting_dict)

                return {
                    "transaction": tx_dict,
                    "location": {
                        "filename": filename,
                        "lineno": lineno
                    }
                }

        raise ValueError(f"Transaction with ID {tx_id} not found")

    def get_accounts(self) -> Set[str]:
        return self.accounts

    def submit_transaction(self, transaction: str, file_path: Optional[str] = None) -> None:
        """Update or add a transaction.

        Args:
            params: The parameters containing the transaction data and optional file path.

        Returns:
            The result of the operation.
        """
        if not transaction:
            raise ValueError("Transaction data is required")

        # Use entrypoint file path if not provided
        if not file_path:
            file_path = str(self.beancount_file)
        else:
            ledger_dir = self.beancount_file.parent
            file_path = str(ledger_dir / file_path)

        if not os.path.exists(file_path):
            raise ValueError(f"File {file_path} does not exist")

        with open(file_path, "a") as f:
            f.write(transaction)

        self.load_beancount_file()


manager: BeancountMCPServer = None

mcp = FastMCP("beancount")


@mcp.tool()
async def beancount_query(query: str) -> str:
    """Execute a BQL (Beancount Query Language) query against the ledger, and return it as a JSON string.
    You can call tool `beancount_accounts` to list accounts from the ledger if needed.
    Example: `SELECT sum(position), account WHERE date>=2024-01-01 GROUP BY account`

    Args:
        query: BQL query string
    """
    logging.info(f"Received BQL query: {query}")
    return json.dumps(manager.query_bql(query), ensure_ascii=False)


@mcp.tool()
async def beancount_get_transaction(tx_id: str) -> str:
    """Get transaction details by ID, and return it as a JSON string.
    If transaction not found, return an error message.

    Args:
        tx_id: Transaction ID
    """
    try:
        return json.dumps(manager.tool_get_transaction(tx_id), ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_accounts() -> str:
    """Get all accounts from the ledger, and return it as a JSON string.

    Args: None

    Returns:
        A JSON string containing a list of accounts.
    """
    try:
        return json.dumps(list(manager.get_accounts()), ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_submit_transaction(transaction: str) -> str:
    """Submit a beancount transaction to the ledger.
    Please make sure the account exists in the ledger, and the transaction date appropriate.
    You can call tool `beancount_accounts` to list accounts from the ledger if needed.
    You can call tool `beancount_current_date` to get current date.

    Example transaction:
    ```
    2025-01-01 * "Grocery Store" "Groceries"
        Expenses:Groceries:SomeGroceryStore 100.00 USD
        Assets:Bank:SomeBank
    ```

    Args:
        transaction: Beancount transaction

    Returns:
        Submit result
    """
    try:
        manager.submit_transaction("\n" + transaction+"\n")
        return json.dumps({"result": "success"}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_current_date() -> str:
    """Get current date from the ledger, and return it as a string.

    Args: None

    Returns:
        A string containing the current date.
    """
    return str(date.today())


@mcp.resource(uri="beancount://accounts", mime_type="application/json", name="All accounts from ledger")
async def accounts() -> str:
    """All accounts from beancount ledger.
    Example:
    ["Assets:Bank:SomeBank","Income:Salary:SomeCompany","Expenses:Groceries:SomeGroceryStore"]
    """
    return json.dumps(list(manager.get_accounts()))


@mcp.resource(uri="beancount://files", mime_type="application/json", name="All files from ledger")
async def files() -> str:
    """All files from beancount ledger.
    Example:
    ["main.bean","txs/2024.bean"]
    """
    return json.dumps(list(manager.resources()))


def init_manager(bean_file: str):
    global manager
    manager = BeancountMCPServer(bean_file)
