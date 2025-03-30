from beancount import loader
from beancount.core import data as d
from beancount.parser.printer import EntryPrinter
from beancount.parser.parser import parse_string

from beancount_mcp.text_editor import ChangeSet, ChangeType, TextEditor


class EntryEditor:
    """A class to edit entries in a Beancount file.

    This class provides methods to edit entries by their unique identifier or by their index.
    It also provides a method to save the changes made to the entries.
    """

    def __init__(self):
        self._entry_printer = EntryPrinter()

    def replace_entry(self, old_entry: d.Directive, new_entry: d.Directive):
        """Replace an entry with a new one."""
        # Implementation of the replace_entry method

        new_entry_text = self._entry_printer(new_entry) + "\n"
        self.replace_entry_with_string(old_entry, new_entry_text, False)

    def replace_entry_with_string(
        self, old_entry: d.Directive, new_entry_text: str, validate_syntax: bool = True
    ):
        """Replace an entry with a new one using a string in beancount syntax."""
        # Implementation of the replace_entry_with_string method
        if validate_syntax:
            _, errors, _ = parse_string(new_entry_text)
            if errors:
                raise ValueError(
                    f"Encountered errors while parsing the new entry: {errors}"
                )

        lineno_range = self._infer_lineno_range(old_entry)
        filename = old_entry.meta["filename"]

        if not new_entry_text.endswith("\n\n"):
            new_entry_text = new_entry_text.rstrip("\n") + "\n\n"

        change = ChangeSet(
            ChangeType.REPLACE,
            lineno_range,
            [new_entry_text],
        )
        editor = TextEditor(filename)
        editor.edit(change)
        editor.save_changes()

    def _infer_lineno_range(self, entry: d.Directive) -> tuple[int, int]:
        file_name = entry.meta["filename"]
        start_lineno = entry.meta["lineno"]
        all_entries, _, _ = loader.load_file(file_name)
        end_lineno = 0
        for e in all_entries:
            lineno = e.meta.get("lineno", 0)
            if lineno > start_lineno and (lineno < end_lineno or end_lineno == 0):
                end_lineno = lineno

        return start_lineno - 1, end_lineno - 1
