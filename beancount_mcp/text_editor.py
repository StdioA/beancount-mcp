# Text file editor


from dataclasses import dataclass
import enum
from math import inf
from pathlib import Path
from types import NoneType
from typing import List, Optional, Tuple


class ChangeType(enum.Enum):
    INSERT = 1
    DELETE = 2
    REPLACE = 3
    APPEND = 4


@dataclass
class ChangeSet:
    """
    Represents a change set that describes a modification to a text file.

    Attributes:
        type (ChangeType): The type of change.

        position (int | Tuple[int, int] | NoneType): The position of the change.
            For INSERT type, it should be an integer representing the position to insert the content.
            For DELETE and REPLACE types, it should be a tuple of two integers representing the range to delete or replace.
            For APPEND type, it should be None, as appending is only supported at the end of the file.

            The line numbers are 0-indexed. When the specified position is negative,
            it is interpreted as a relative position from the end of the file:
            e.g. (pos, -1) means every line from pos to the end of the file, inclusive;
            (pos, -2) means every line from pos to the second last line, etc.

        content (Optional[List[str]]): The content to be inserted, replaced, or appended.
            This attribute is required for INSERT, REPLACE, and APPEND types.
    """

    type: ChangeType
    position: int | Tuple[int, int] | NoneType = None
    content: Optional[List[str]] = None

    def __post_init__(self):
        # Verify content
        if (
            self.type in {ChangeType.INSERT, ChangeType.REPLACE, ChangeType.APPEND}
            and self.content is None
        ):
            raise ValueError(f"content is required for {self.type}")

        # Verify position
        if self.type in {ChangeType.INSERT} and not isinstance(self.position, int):
            raise ValueError(f"position must be an integer for {self.type}")

        if self.type in {ChangeType.DELETE, ChangeType.REPLACE} and \
                not (isinstance(self.position, tuple) and len(self.position) == 2):
            raise ValueError(f"position must be a tuple of two integers for {self.type} with (begin, end)")

        if self.type in {ChangeType.APPEND} and self.position is not None:
            raise ValueError(f"position must be None for {self.type}, as we only support appending to the end of the file.")

        if not isinstance(self.content, list | NoneType):
            raise ValueError(f"content must be a list of strings for {self.type}")

    def __repr__(self) -> str:
        return f"ChangeSet(\ntype={self.type},\nposition={self.position},\ncontent={self.content})"


class TextEditor:
    def __init__(self, file_path: str, encoding: str = "utf-8") -> None:
        """
        Initialize a TextEditor with given file path.

        Args:
            file_path (str): The path to the file.

        Raises:
            AssertionError: If the file does not exist.
        """
        self._file_path = Path(file_path)
        self._encoding = encoding
        self._lines = self._read_file(file_path)
        self._file_n_lines = len(self._lines)
        if not self._file_path.exists():
            raise AssertionError(f"File {file_path} does not exist.")
        self._changes = []

    def _read_file(self, file_path: str) -> List[str]:
        with Path.open(file_path, "r", encoding=self._encoding) as file:
            lines = file.readlines()
        lines.append(
            ""
        )  # note: we add this empty line to be able to address the position after the last line with -1
        return lines

    def edit(self, changes: List[ChangeSet] | ChangeSet) -> None:
        """
        Applies the given changes to the text editor.

        Args:
            changes (List[ChangeSet] | ChangeSet): The changes to be applied. It can be a single ChangeSet or a list of ChangeSet objects.

        Returns:
            None
        """
        if isinstance(changes, ChangeSet):
            changes = [changes]
        self._changes.extend(changes)

    def _get_position_tuple(self, change: ChangeSet) -> Tuple[int, int]:
        # convert relative positions to their absolute values
        if change.type == ChangeType.INSERT:
            pos_tuple = (change.position, change.position)
        elif change.type == ChangeType.DELETE or change.type == ChangeType.REPLACE:
            pos_tuple = change.position
        else:
            pos_tuple = (inf, inf)

        if pos_tuple[0] < 0:
            pos_tuple = (self._file_n_lines + pos_tuple[0], pos_tuple[1])
        if pos_tuple[1] < 0:
            pos_tuple = (pos_tuple[0], self._file_n_lines + pos_tuple[1])

        return pos_tuple

    def _sort_changes_by_position(self):
        self._changes.sort(key=self._get_position_tuple)

    def _check_changes_non_overlapping(self):
        for idx in range(len(self._changes) - 1):
            edit_begin, edit_end = self._get_position_tuple(self._changes[idx])
            next_begin, next_end = self._get_position_tuple(self._changes[idx + 1])
            if edit_begin <= next_begin < edit_end:
                raise ValueError(f"Changes {self._changes[idx]} and {self._changes[idx + 1]} are overlapping.")

            if edit_begin == edit_end and (next_begin == next_end == edit_begin):
                raise ValueError(f"Double insertion at position {edit_begin} detected.")

    def _check_range_validity(self, line_count: int):
        for change in self._changes:
            position = self._get_position_tuple(change)
            if not (position == (inf, inf) or (0 <= position[0] <= position[1] < line_count)):
                raise ValueError(f"Change {change} is invalid.")

    def save_changes(self, to_path: Optional[str] = None):
        """
        Saves the changes made to the file.

        Args:
            to_path (Optional[str]): The path to save the file. If not provided, the changes will be saved to the original file path.

        Returns:
            None
        """

        lines = self._lines

        self._sort_changes_by_position()
        self._check_changes_non_overlapping()
        self._check_range_validity(len(lines))

        edited_lines = []

        change_idx = 0
        line_idx = 0
        while (
            line_idx < len(lines) - 1
        ):  # ignore the last empty line added when reading
            # No more changes to apply
            if change_idx >= len(self._changes):
                edited_lines.extend(lines[line_idx:])
                break

            change_begin, change_end = self._get_position_tuple(
                self._changes[change_idx]
            )

            # Next change is append
            if change_begin == inf:
                edited_lines.extend(lines[line_idx:])
                edited_lines.extend(self._changes[change_idx].content)
                break

            # Next change is after this line
            if line_idx < change_begin:
                edited_lines.extend(lines[line_idx:change_begin])
                line_idx = change_begin
                continue

            # Next change is on this line
            if self._changes[change_idx].type == ChangeType.INSERT:
                edited_lines.extend(self._changes[change_idx].content)
                change_idx += 1
                continue
            if self._changes[change_idx].type == ChangeType.DELETE:
                line_idx = change_end
                change_idx += 1
                continue
            if self._changes[change_idx].type == ChangeType.REPLACE:
                edited_lines.extend(self._changes[change_idx].content)
                line_idx = change_end
                change_idx += 1
                continue
            if self._changes[change_idx].type == ChangeType.APPEND:
                edited_lines.append(self._changes[change_idx].content)
                change_idx += 1
                continue

            raise AssertionError(f"Unexpected change type {self._changes[change_idx].type}")

        save_path = to_path if to_path is not None else self._file_path
        with Path.open(save_path, "w", encoding=self._encoding) as file:
            file.writelines(edited_lines)
