"""
Cleanup utilities for tracklistify.

Provides functions to clean up generated directories (cache, output, logs)
with interactive confirmation and size reporting.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)


def get_dir_size(path: Path) -> int:
    """Calculate total size of a directory in bytes.

    Args:
        path: Path to the directory

    Returns:
        Total size in bytes, 0 if directory doesn't exist
    """
    if not path.exists():
        return 0

    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass

    return total


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size.

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable string like "142.3 MB"
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def clean_directory(path: Path, dry_run: bool = False) -> int:
    """Remove all contents of a directory.

    Args:
        path: Path to the directory to clean
        dry_run: If True, don't actually delete anything

    Returns:
        Number of bytes freed (or would be freed in dry_run mode)
    """
    if not path.exists():
        return 0

    size = get_dir_size(path)

    if dry_run:
        return size

    try:
        # Remove all contents
        for item in path.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except (OSError, PermissionError) as e:
                logger.warning(f"Could not remove {item}: {e}")

        logger.info(f"Cleaned {path}: {format_size(size)} freed")
        return size

    except Exception as e:
        logger.error(f"Error cleaning {path}: {e}")
        return 0


@dataclass
class CleanupTarget:
    """Represents a directory that can be cleaned."""

    name: str
    path: Path
    size: int
    exists: bool

    @property
    def size_formatted(self) -> str:
        return format_size(self.size)


class CleanupManager:
    """Manages cleanup of tracklistify directories."""

    # Target names that can be cleaned
    TARGETS = ["cache", "output", "logs", "temp", "segments"]

    def __init__(self, config=None):
        """Initialize the cleanup manager.

        Args:
            config: Optional config object. If not provided, will load from factory.
        """
        if config is None:
            from tracklistify.config.factory import get_config

            config = get_config()

        self.config = config

        # Map target names to config paths
        # "segments" is an alias for "temp" (audio segments directory)
        self._target_paths = {
            "cache": Path(config.cache_dir),
            "output": Path(config.output_dir),
            "logs": Path(config.log_dir),
            "temp": Path(config.temp_dir),
            "segments": Path(config.temp_dir),  # Alias for temp
        }

    def get_target(self, name: str) -> Optional[CleanupTarget]:
        """Get information about a cleanup target.

        Args:
            name: Target name (cache, output, logs, temp)

        Returns:
            CleanupTarget or None if invalid name
        """
        if name not in self._target_paths:
            return None

        path = self._target_paths[name]
        exists = path.exists()
        size = get_dir_size(path) if exists else 0

        return CleanupTarget(
            name=name,
            path=path,
            size=size,
            exists=exists,
        )

    def summarize(
        self, targets: Optional[List[str]] = None
    ) -> Dict[str, CleanupTarget]:
        """Get summary of cleanup targets.

        Args:
            targets: List of target names to include, or None for all

        Returns:
            Dict mapping target name to CleanupTarget
        """
        if targets is None:
            targets = self.TARGETS

        result = {}
        for name in targets:
            target = self.get_target(name)
            if target:
                result[name] = target

        return result

    def print_summary(self, targets: Optional[List[str]] = None) -> int:
        """Print a summary of cleanup targets to stdout.

        Args:
            targets: List of target names to include, or None for all

        Returns:
            Total size in bytes
        """
        summary = self.summarize(targets)

        if not summary:
            print("No cleanup targets found.")
            return 0

        # Find max path length for alignment
        max_name_len = max(len(t.name) for t in summary.values())
        max_path_len = max(len(str(t.path)) for t in summary.values())

        print("\nCleanup targets:")
        total_size = 0

        for target in summary.values():
            if target.exists:
                status = target.size_formatted.rjust(10)
                total_size += target.size
            else:
                status = "(empty)".rjust(10)

            name = target.name.capitalize().ljust(max_name_len + 2)
            path = str(target.path).ljust(max_path_len)
            print(f"  {name} {path}  {status}")

        print("  " + "─" * (max_name_len + max_path_len + 16))
        print(f"  {'Total'.ljust(max_name_len + max_path_len + 4)} {format_size(total_size).rjust(10)}")
        print()

        return total_size

    def confirm(self, message: str = "Proceed?") -> bool:
        """Ask for user confirmation.

        Args:
            message: Confirmation message

        Returns:
            True if user confirmed, False otherwise
        """
        try:
            response = input(f"{message} [y/N]: ").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    def interactive_select(self) -> List[str]:
        """Interactively select which targets to clean.

        Shows all available targets with their sizes and lets user
        toggle selection with numbers, then confirm.

        Returns:
            List of selected target names
        """
        # Get all targets (exclude "segments" alias, use "temp" instead)
        base_targets = ["cache", "output", "logs", "temp"]
        summary = self.summarize(base_targets)

        if not summary:
            print("No cleanup targets available.")
            return []

        # Check if any have content
        targets_with_content = [t for t in summary.values() if t.exists and t.size > 0]
        if not targets_with_content:
            print("All directories are already empty.")
            return []

        # Display names for user
        display_names = {
            "cache": "Cache (downloads & identifications)",
            "output": "Output (tracklist files)",
            "logs": "Logs",
            "temp": "Segments (audio segments)",
        }

        # Track selection state
        selected = {name: False for name in base_targets}

        while True:
            print("\n" + "=" * 50)
            print("Select items to clean (toggle with number):")
            print("=" * 50)

            # Count how many have content and are selected
            targets_with_content = []
            for name in base_targets:
                target = summary.get(name)
                if target and target.exists and target.size > 0:
                    targets_with_content.append(name)

            for idx, name in enumerate(base_targets, 1):
                target = summary.get(name)
                if target and target.exists and target.size > 0:
                    checkbox = "[x]" if selected[name] else "[ ]"
                    size_str = target.size_formatted.rjust(10)
                    display = display_names.get(name, name)
                    print(f"  {idx}. {checkbox} {display} {size_str}")
                elif target and target.exists:
                    print(f"  {idx}. [-] {display_names.get(name, name)} (empty)")
                else:
                    print(f"  {idx}. [-] {display_names.get(name, name)} (not found)")

            # Option 5: All
            all_selected = all(selected.get(name, False) for name in targets_with_content)
            all_checkbox = "[x]" if all_selected and targets_with_content else "[ ]"
            total_size = sum(summary[name].size for name in targets_with_content)
            print(f"\n  5. {all_checkbox} Alle {format_size(total_size).rjust(10)}")

            # Show total selected
            selected_targets = [t for name, t in summary.items() if selected.get(name)]
            total_selected = sum(t.size for t in selected_targets if t)
            print(f"\n  Selected: {format_size(total_selected)}")
            print()
            print("  Enter: 1-5 toggle, 'c' confirm, 'q' quit")

            try:
                choice = input("\n  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                return []

            if choice == "q":
                print("Cancelled.")
                return []
            elif choice == "c":
                selected_names = [name for name, sel in selected.items() if sel]
                if not selected_names:
                    print("Nothing selected. Use numbers to toggle selection.")
                    continue
                return selected_names
            elif choice == "5":
                # Toggle all: if all selected -> deselect all, else select all
                if all_selected:
                    for name in base_targets:
                        selected[name] = False
                else:
                    for name in targets_with_content:
                        selected[name] = True
            elif choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(base_targets):
                    name = base_targets[idx - 1]
                    target = summary.get(name)
                    if target and target.exists and target.size > 0:
                        selected[name] = not selected[name]
                    else:
                        print(f"  '{name}' is empty or not found.")
                else:
                    print(f"  Invalid number. Use 1-5.")
            else:
                print("  Invalid input. Use 1-5, 'c', or 'q'.")

    def clean(
        self,
        targets: Optional[List[str]] = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> Tuple[int, int]:
        """Clean the specified targets.

        Args:
            targets: List of target names to clean, or None for all
            force: If True, skip confirmation
            dry_run: If True, don't actually delete anything

        Returns:
            Tuple of (bytes_freed, num_targets_cleaned)
        """
        if targets is None:
            targets = ["cache", "output", "logs"]  # Exclude temp by default

        summary = self.summarize(targets)

        if not summary:
            print("No targets to clean.")
            return 0, 0

        # Check if anything to clean
        existing_targets = [t for t in summary.values() if t.exists and t.size > 0]
        if not existing_targets:
            print("All targets are already empty.")
            return 0, 0

        # Show summary and confirm
        total_size = self.print_summary(targets)

        if not force:
            target_names = ", ".join(t.name for t in existing_targets)
            if not self.confirm(f"Delete {target_names}?"):
                print("Cancelled.")
                return 0, 0

        if dry_run:
            print("Dry run - no files deleted.")
            return total_size, len(existing_targets)

        # Perform cleanup
        bytes_freed = 0
        targets_cleaned = 0

        for target in existing_targets:
            freed = clean_directory(target.path)
            if freed > 0:
                bytes_freed += freed
                targets_cleaned += 1
                print(f"  Cleaned {target.name}: {format_size(freed)} freed")

        print(f"\nTotal freed: {format_size(bytes_freed)}")
        return bytes_freed, targets_cleaned
