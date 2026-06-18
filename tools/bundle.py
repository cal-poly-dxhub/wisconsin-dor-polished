#!/usr/bin/env python3

import os
import shutil
import tomllib
from pathlib import Path

# Global configuration
BUNDLE_CONFIG_FILE = "bundles.toml"

# ANSI color codes for colored output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'

def colored_print(text: str, color: str = Colors.RESET) -> None:
    """Print text with color."""
    print(f"{color}{text}{Colors.RESET}")

# Patterns to ignore during bundling
IGNORE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".DS_Store",
    ".git",
    ".gitignore",
    "*.egg-info",
    ".coverage",
    "node_modules",
]


def should_ignore(path: str) -> bool:
    """Check if a path should be ignored based on ignore patterns."""
    basename = os.path.basename(path)

    # Check exact matches
    if basename in IGNORE_PATTERNS:
        return True

    # Check wildcard patterns
    for pattern in IGNORE_PATTERNS:
        if "*" in pattern:
            # Simple wildcard matching for *.ext patterns
            if pattern.startswith("*.") and basename.endswith(pattern[1:]):
                return True

    return False


def main():
    """Main bundling function."""
    # Read configuration from TOML file
    config_path = Path(BUNDLE_CONFIG_FILE)
    if not config_path.exists():
        colored_print(f"Error: Configuration file '{BUNDLE_CONFIG_FILE}' not found!", Colors.RED)
        return 1

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        colored_print(f"Error reading configuration file: {e}", Colors.RED)
        return 1

    bundles = config.get("bundles", [])
    if not bundles:
        colored_print("No bundles found in configuration file.", Colors.YELLOW)
        return 0

    # Get target directory from config, default to "bundle"
    target_dir = config.get("target_directory", "bundle")
    
    colored_print(f"Starting bundling process...", Colors.BOLD + Colors.CYAN)
    colored_print(f"Target directory: {target_dir}", Colors.BLUE)
    colored_print(f"Found {len(bundles)} bundle(s) to process\n", Colors.BLUE)

    # Create target directory
    os.makedirs(target_dir, exist_ok=True)

    total_files = 0

    for i, bundle in enumerate(bundles, 1):
        dest = os.path.join(target_dir, bundle['dest'])

        # Create target directory if it doesn't exist
        os.makedirs(dest, exist_ok=True)

        colored_print(f"[{i}/{len(bundles)}] Bundling to {dest}:", Colors.BOLD + Colors.MAGENTA)

        # Copy files
        for source in bundle["sources"]:
            source_path = source
            if os.path.exists(source_path):
                if os.path.isdir(source_path):
                    # Copy directory contents
                    for item in os.listdir(source_path):
                        s = os.path.join(source_path, item)
                        d = os.path.join(dest, item)

                        # Skip ignored patterns
                        if should_ignore(s):
                            colored_print(f"  Skipped (ignored): {item}", Colors.YELLOW)
                            continue

                        if os.path.isdir(s):
                            shutil.copytree(
                                s,
                                d,
                                dirs_exist_ok=True,
                                ignore=lambda dir, files: [
                                    f for f in files if should_ignore(os.path.join(dir, f))
                                ],
                            )
                            colored_print(f"  Copied directory: {item}", Colors.GREEN)
                        else:
                            shutil.copy2(s, d)
                            colored_print(f"  Copied file: {item}", Colors.GREEN)
                else:
                    # Copy single file
                    if should_ignore(source_path):
                        colored_print(f"  Skipped (ignored): {os.path.basename(source_path)}", Colors.YELLOW)
                        continue

                    filename = os.path.basename(source_path)
                    d = os.path.join(dest, filename)
                    shutil.copy2(source_path, d)
                    colored_print(f"  Copied file: {filename}", Colors.GREEN)
            else:
                colored_print(f"  Warning: Source path does not exist: {source_path}", Colors.RED)

        # Count files in this destination
        file_count = sum(len(files) for _, _, files in os.walk(dest))
        total_files += file_count

        colored_print(f"  Total files in {dest}: {file_count}\n", Colors.CYAN)

    colored_print(f"Bundling complete! Total files bundled: {total_files}", Colors.BOLD + Colors.GREEN)
    return 0


if __name__ == "__main__":
    exit(main())
