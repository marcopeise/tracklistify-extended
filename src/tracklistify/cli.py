# Standard library imports
import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from .config import ConfigError, get_config, get_root
from .core import ApplicationError, AsyncApp

# Local/package imports
from .utils.cleanup import CleanupManager
from .utils.logger import get_logger, set_logger

# Get the logger for this module
logger = get_logger(__name__)


async def main(args: argparse.Namespace) -> int:
    """Main entry point."""
    app = None  # Initialize app to None
    try:
        # Load configuration
        config = get_config()

        # Apply CLI overrides to config
        if args.formats:
            config.output_format = args.formats
        if args.provider:
            config.primary_provider = args.provider
        if args.no_fallback:
            config.fallback_enabled = False
        if args.output:
            config.output_dir = Path(args.output).resolve()
            config.output_dir.mkdir(parents=True, exist_ok=True)
        if getattr(args, "delete_segments", False):
            config.keep_segments = False

        # Create and run application
        app = AsyncApp(config)

        # Setup signal handlers — cancel main task on Ctrl+C
        main_task = asyncio.current_task()

        def signal_handler():
            logger.info("Received shutdown signal, cancelling...")
            if main_task and not main_task.done():
                main_task.cancel()

        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_event_loop().add_signal_handler(sig, signal_handler)

        # Get optional direct 1001tracklists URL
        direct_1001_url = getattr(args, "direct_1001_url", None)

        # Process input
        await app.process_input(args.input, direct_1001_url=direct_1001_url)

        return 0

    except asyncio.CancelledError:
        logger.info("Operation cancelled by user")
        print("\nOperation cancelled by user")
        return 1
    except ConfigError as e:
        logger.error(f"Configuration error: {e}", exc_info=True)
        return 1
    except ApplicationError as e:
        logger.error(f"Application error: {e}", exc_info=True)
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1
    finally:
        if app:
            await app.close()


def clean_command(args: argparse.Namespace) -> int:
    """Execute the clean command.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success)
    """
    try:
        config = get_config()
        manager = CleanupManager(config)

        # Determine which targets to clean
        targets: Optional[List[str]] = None

        # Interactive mode: let user select
        if getattr(args, "interactive", False):
            targets = manager.interactive_select()
            if not targets:
                return 0  # User cancelled or nothing selected
        elif args.all:
            targets = ["cache", "output", "logs", "temp"]  # "temp" = audio segments
        else:
            targets = []
            if args.cache:
                targets.append("cache")
            if args.output:
                targets.append("output")
            if args.logs:
                targets.append("logs")
            if getattr(args, "segments", False):
                targets.append("temp")  # "temp" directory contains audio segments

            # If no specific targets, use interactive selection
            if not targets:
                targets = manager.interactive_select()
                if not targets:
                    return 0  # User cancelled or nothing selected

        # Perform cleanup (skip confirmation in interactive mode - already confirmed)
        bytes_freed, num_cleaned = manager.clean(
            targets=targets,
            force=args.force or getattr(args, "interactive", False),
            dry_run=args.dry_run,
        )

        return 0

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        print(f"Error: {e}")
        return 1


def parse_args() -> argparse.Namespace:
    """Parse command line arguments with subcommand support.

    Supports both:
    - `tracklistify <url>` (backward compatible, implicit run)
    - `tracklistify run <url>`
    - `tracklistify clean [--cache] [--output] [--logs] [--all] [--force]`
    """
    parser = argparse.ArgumentParser(
        description="Tracklistify - Automatic tracklist generator for DJ mixes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tracklistify https://soundcloud.com/artist/mix   Identify tracks in a mix
  tracklistify run -f markdown my_mix.mp3          Identify and output as markdown
  tracklistify clean                               Clean all generated files
  tracklistify clean --cache --force               Clean only cache without confirmation
        """,
    )

    # Global arguments (before subcommand)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )

    parser.add_argument(
        "--log-file",
        default=None,
        type=Path,
        help="Log file path",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        default=True,
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "-d",
        "--debug",
        default=False,
        action="store_true",
        help="Enable debug logging",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ─────────────────────────────────────────────────────────────────────────
    # 'run' subcommand (main track identification)
    # ─────────────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help="Identify tracks in an audio file or URL",
        description="Identify tracks in an audio file or URL",
    )

    run_parser.add_argument(
        "input",
        help="Path to audio file or yt-dlp URL",
    )

    run_parser.add_argument(
        "-f",
        "--formats",
        default="all",
        choices=["json", "markdown", "m3u", "all"],
        help="Output format(s)",
    )

    run_parser.add_argument(
        "-p",
        "--provider",
        help="Specify the primary track identification provider",
    )

    run_parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable fallback to secondary providers",
    )

    run_parser.add_argument(
        "-o",
        "--output",
        default=None,
        type=Path,
        help="Output directory for generated tracklists",
    )

    run_parser.add_argument(
        "--progress",
        action="store_true",
        default=False,
        help="Show progress with detailed status",
    )

    run_parser.add_argument(
        "-b",
        "--batch",
        action="store_true",
        default=False,
        help="Enable batch processing mode",
    )

    run_parser.add_argument(
        "--delete-segments",
        action="store_true",
        default=False,
        help="Delete audio segments after analysis (default: keep them, use 'clean --segments' later)",
    )

    run_parser.add_argument(
        "--1001tracklists",
        dest="direct_1001_url",
        metavar="URL_OR_FILE",
        default=None,
        help="1001tracklists.com URL or saved HTML file (skips search). "
             "Use HTML file if bot protection blocks direct access.",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 'clean' subcommand
    # ─────────────────────────────────────────────────────────────────────────
    clean_parser = subparsers.add_parser(
        "clean",
        help="Clean generated files and cache",
        description="Remove generated files, cache, and logs",
    )

    clean_parser.add_argument(
        "--cache",
        action="store_true",
        help="Clean download cache and identification cache",
    )

    clean_parser.add_argument(
        "--output",
        action="store_true",
        help="Clean output files (tracklists)",
    )

    clean_parser.add_argument(
        "--logs",
        action="store_true",
        help="Clean log files",
    )

    clean_parser.add_argument(
        "--segments",
        action="store_true",
        help="Clean audio segments (temp directory)",
    )

    clean_parser.add_argument(
        "--all",
        action="store_true",
        help="Clean everything (cache + output + logs + segments)",
    )

    clean_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Skip confirmation prompt",
    )

    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )

    clean_parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactively select which items to clean",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Handle backward compatibility: `tracklistify <url>` without subcommand
    # Also handles: `tracklistify --progress -o dir <url>`
    # ─────────────────────────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        # Check if 'run' or 'clean' is already explicitly specified
        has_subcommand = "run" in sys.argv or "clean" in sys.argv

        if not has_subcommand:
            # Run-specific flags that indicate user wants 'run' command
            run_flags = {
                "-f", "--formats", "-p", "--provider", "--no-fallback",
                "-o", "--output", "--progress", "-b", "--batch",
                "--delete-segments", "--1001tracklists"
            }

            # Global flags (these don't indicate a specific command)
            global_flags = {
                "-h", "--help", "-v", "--verbose", "-d", "--debug",
                "--log-level", "--log-file"
            }

            # Check if any run-specific flag is present
            has_run_flag = any(arg in run_flags for arg in sys.argv)

            # Check if there's a positional argument (URL or file path)
            # that's not a flag or known command
            has_positional = False
            for arg in sys.argv[1:]:
                if not arg.startswith("-") and arg not in ["run", "clean"]:
                    has_positional = True
                    break

            # Insert 'run' if we have run-specific flags OR a positional argument
            if has_run_flag or has_positional:
                # Find the right position to insert 'run'
                # It should go after global flags but before run-specific flags
                insert_pos = 1
                i = 1
                while i < len(sys.argv):
                    arg = sys.argv[i]
                    if arg in global_flags:
                        insert_pos = i + 1
                        # Skip the value for flags that take arguments
                        if arg in ("--log-level", "--log-file") and i + 1 < len(sys.argv):
                            insert_pos = i + 2
                            i += 1
                    elif arg.startswith("-") and arg in run_flags:
                        # Found a run flag, insert 'run' before it
                        break
                    elif not arg.startswith("-"):
                        # Found positional, insert 'run' before it
                        break
                    i += 1

                sys.argv.insert(insert_pos, "run")

    args = parser.parse_args()

    # If no command specified (just `tracklistify`), show help
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    return args


def load_environment_variables(env_path: Path) -> None:
    """Load environment variables from a file."""
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")

        # Log loaded environment variables for debugging
        for key, value in os.environ.items():
            if key.startswith("TRACKLISTIFY_"):
                logger.debug(f"Loaded env var: {key}={value}")


def cli() -> None:
    """Core CLI execution logic"""
    args = parse_args()

    # Setup logging
    set_logger(
        log_level=args.log_level,
        log_file=args.log_file,
        verbose=args.verbose,
        debug=args.debug,
    )

    # Load environment variables first
    env_path = get_root() / ".env"
    load_environment_variables(env_path)

    try:
        # Route to appropriate command handler
        if args.command == "clean":
            logger.info("Starting cleanup")
            exit_code = clean_command(args)
        elif args.command == "run":
            logger.info("Starting track identification")
            exit_code = asyncio.run(main(args))
        else:
            # Should not reach here due to parse_args handling
            print("Unknown command. Use --help for usage.")
            exit_code = 1

        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        print("\nOperation cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    """Main entry point"""
    cli()
