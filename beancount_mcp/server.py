"""Beancount MCP Server implementation."""

import re
import json
import logging
import time
from datetime import datetime
from pathlib import Path
import signal
from typing import Dict, List, Any, Optional

from beancount import loader
from beanquery.query import run_query
from beancount.core import data, getters
from beancount.core.compare import hash_entry
from beancount.parser.printer import EntryPrinter
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from beancount_mcp.entry_editor import EntryEditor
from mcp.server.fastmcp import FastMCP


logger = logging.getLogger(__name__)


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
        logger.info("Detected changes in %s, reloading...", event.src_path)
        try:
            self.server.load_beancount_file()
        except Exception as e:
            logger.error("Error reloading Beancount file: %s", e)


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
        self.entry_editor = EntryEditor()
        self.setup_file_watcher()
        self.printer = EntryPrinter()

    def load_beancount_file(self):
        """Load the Beancount file and extract necessary data."""
        try:
            self.entries, self.errors, self.options_map = loader.load_file(
                str(self.beancount_file)
            )
            self.accounts = getters.get_accounts(self.entries)
            self.last_load_time = time.time()
            if self.errors:
                logger.error("Found %d errors in the Beancount file", len(self.errors))
        except Exception as e:
            logger.error("Error loading Beancount file: %s", e)
            raise

    def setup_file_watcher(self):
        """Setup a file watcher to monitor changes to the Beancount file."""
        self.observer = Observer()
        event_handler = BeancountFileHandler(self)
        self.observer.schedule(
            event_handler, str(self.beancount_file.parent), recursive=True
        )
        self.observer.start()
        logger.info("Started file watcher for %s", self.beancount_file.parent)

    def shutdown_file_watcher(self):
        logger.info("Shutting down file watcher")
        self.observer.stop()
        self.observer.join()

    @property
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

    def query_bql(self, query_string: str) -> Dict[str, List[Any]]:
        """Execute a BQL query.
        Example:
        SELECT id, sum(position), account FROM OPEN ON 2024-01-01 CLOSE ON 2024-12-31 WHERE narration="lunch" AND account~"Expenses:Daily:Foods"

        Args:
            params: The parameters for the query.

        Returns:
            The query results.
        """
        if not query_string:
            raise ValueError("Query parameter is required")

        # Some tricky stuff to make BQL query work
        # In case LLM wrote date with quote like `WHERE date > '2025-04-01'`
        pattern = re.compile(r'[\'"](\d{4}-\d{2}-\d{2})[\'"]')
        query_string = re.sub(pattern, r"\1", query_string)
        # In case LLM wrote query like SQL: `SELECT sum(position) FROM transactions`
        from_pattern = re.compile(r"FROM transactions?")
        query_string = re.sub(from_pattern, "", query_string)
        try:
            types, rows = run_query(self.entries, self.options_map, query_string)
            column_names = [
                {
                    "name": t.name,
                    "type": f"{t.datatype.__module__}.{t.datatype.__qualname__}",
                }
                for t in (types or [])
            ]

            return {
                "columns": column_names,
                "rows": [[str(c) for c in r] for r in rows[:200]],
            }
        except Exception as e:
            raise ValueError("BQL query error: %s", e) from e

    def get_transaction(self, tx_id: str) -> Dict[str, Any]:
        """Get transaction details by ID.

        Args:
            params: The parameters containing the transaction ID.

        Returns:
            The transaction details and file location.
        """
        if not tx_id:
            raise ValueError("Transaction ID is required")

        for entry in self.entries:
            if isinstance(entry, data.Transaction) and hash_entry(entry) == tx_id:
                filename = entry.meta.get("filename")
                lineno = entry.meta.get("lineno")

                return {
                    "transaction": self.printer(entry),
                    "location": {"filename": filename, "lineno": lineno},
                }

        raise ValueError(f"Transaction with ID {tx_id} not found")

    def submit_transaction(
        self, transaction: str, file_path: Optional[str] = None
    ) -> None:
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

        if not Path.exists(file_path):
            raise ValueError(f"File {file_path} does not exist")

        with Path.open(file_path, "a") as f:
            f.write(transaction)

        self.load_beancount_file()

    def replace_transaction(self, tx_id: str, transaction: str) -> None:
        """Replace an existing transaction.

        Args:
            params: The parameters containing the transaction ID and new data.

        Returns:
            The result of the operation.
        """
        if not tx_id:
            raise ValueError("Transaction ID is required")

        for entry in self.entries:
            if isinstance(entry, data.Transaction) and hash_entry(entry) == tx_id:
                # Update the transaction with new data
                old_transaction = entry
                break
        else:
            raise ValueError(f"Transaction with ID {tx_id} not found")

        self.entry_editor.replace_entry_with_string(old_transaction, transaction)


manager: Optional[BeancountMCPServer] = None


def init_manager(bean_file: str):
    global manager
    manager = BeancountMCPServer(bean_file)


def signal_handler(sig, frame):
    if manager:
        manager.shutdown_file_watcher()


signal.signal(signal.SIGINT, signal_handler)


mcp = FastMCP("beancount")


@mcp.tool()
async def beancount_query(query: str) -> str:
    """Execute a BQL (Beancount Query Language) query against the ledger, and return it as a JSON string.
    You can call tool `beancount_accounts` to list accounts from the ledger if needed.
    Example: `SELECT sum(position), account WHERE date>=2024-01-01 GROUP BY account`

    Args:
        query: BQL query string
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    logger.info("Received BQL query: %s", query)
    return json.dumps(manager.query_bql(query), ensure_ascii=False)


@mcp.tool()
async def beancount_get_transaction(tx_id: str) -> str:
    """Get transaction details by ID, and return it as a JSON string.
    If transaction not found, return an error message.

    Args:
        tx_id: Transaction ID
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    try:
        return json.dumps(manager.get_transaction(tx_id), ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_accounts() -> str:
    """Get all accounts from the ledger, and return it as a JSON string.

    Args: None

    Returns:
        A JSON string containing a list of accounts.
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    try:
        return json.dumps(list(manager.accounts), ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_submit_transaction(transaction: str) -> str:
    """Submit a beancount transaction to the ledger.
    Please make sure the account exists in the ledger, and the transaction date appropriate.
    You SHOULD call tool `beancount_accounts` to list accounts from the ledger if needed.
    You SHOULD call tool `beancount_current_date` to get current date.

    Example transaction:
    ```
    2025-01-01 * "Grocery Store" "Groceries"
        Assets:Bank:SomeBank
        Expenses:Groceries:SomeGroceryStore 100.00 USD
    ```

    Args:
        transaction: Beancount transaction

    Returns:
        Submit result
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    try:
        manager.submit_transaction("\n" + transaction + "\n")
        return json.dumps({"result": "success"}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_replace_transaction(tx_id: str, transaction: str) -> str:
    """Replace a beancount transaction in the ledger.
    Please make sure the account exists in the ledger, and the transaction date appropriate.
    You SHOULD call tool `beancount_accounts` to list accounts from the ledger if needed.
    You SHOULD call tool `beancount_current_date` to get current date.

    Example transaction:
    ```
    2025-01-01 * "Payee" "Narration" #optional_tag
        Assets:Bank:SomeBank -100 EUR
        Expenses:Groceries:SomeGroceryStore
    ```

    Args:
        tx_id: Transaction ID
        transaction: Beancount transaction
    Returns:
        Replace result
    """
    try:
        manager.replace_transaction(
            tx_id,
            transaction,
        )
    except AssertionError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def beancount_current_date() -> str:
    """Get current date from the ledger, and return it as a string.

    Args: None

    Returns:
        A string containing the current date.
    """
    today = datetime.now().astimezone().date()
    return str(today)


@mcp.resource(
    uri="beancount://accounts",
    mime_type="application/json",
    name="All accounts from ledger",
)
async def accounts() -> str:
    """All accounts from beancount ledger.
    Example:
    ["Assets:Bank:SomeBank","Income:Salary:SomeCompany","Expenses:Groceries:SomeGroceryStore"]
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    return json.dumps(list(manager.accounts), ensure_ascii=False)


@mcp.resource(
    uri="beancount://files", mime_type="application/json", name="All files from ledger"
)
async def files() -> str:
    """All files from beancount ledger.
    Example:
    ["main.bean","txs/2024.bean"]
    """
    if manager is None:
        return json.dumps({"error": "Beancount manager is not initialized"})

    return json.dumps(list(manager.resources), ensure_ascii=False)
