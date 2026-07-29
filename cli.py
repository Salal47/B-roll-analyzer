"""
CLI entrypoint — run as: python cli.py /path/to/clips
Works locally, in a Docker container, or as a GitHub Actions / cron job.
"""
import sys
from core import process_broll_folder


def main():
    if len(sys.argv) < 2:
        print("Usage: python cli.py /path/to/clips/folder")
        sys.exit(1)
    process_broll_folder(sys.argv[1])


if __name__ == "__main__":
    main()
