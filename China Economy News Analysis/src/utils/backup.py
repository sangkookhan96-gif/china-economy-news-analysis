#!/usr/bin/env python3
"""Database backup utility."""

import shutil
import gzip
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import DATABASE_PATH, BACKUP_PATH


def create_backup(compress: bool = True) -> Path:
    """Create a backup of the database.

    Args:
        compress: Whether to gzip compress the backup

    Returns:
        Path to the backup file
    """
    db_path = Path(DATABASE_PATH)
    backup_dir = Path(BACKUP_PATH)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if compress:
        backup_filename = f"news_backup_{timestamp}.db.gz"
        backup_path = backup_dir / backup_filename

        with open(db_path, 'rb') as f_in:
            with gzip.open(backup_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        backup_filename = f"news_backup_{timestamp}.db"
        backup_path = backup_dir / backup_filename
        shutil.copy2(db_path, backup_path)

    print(f"Backup created: {backup_path}")
    return backup_path


def restore_backup(backup_path: str) -> bool:
    """Restore database from backup.

    Args:
        backup_path: Path to the backup file

    Returns:
        True if successful
    """
    backup_file = Path(backup_path)
    db_path = Path(DATABASE_PATH)

    if not backup_file.exists():
        print(f"Backup file not found: {backup_path}")
        return False

    # Create a backup of current DB before restoring
    if db_path.exists():
        current_backup = db_path.with_suffix('.db.bak')
        shutil.copy2(db_path, current_backup)
        print(f"Current DB backed up to: {current_backup}")

    if backup_file.suffix == '.gz':
        with gzip.open(backup_file, 'rb') as f_in:
            with open(db_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copy2(backup_file, db_path)

    print(f"Database restored from: {backup_path}")
    return True


def cleanup_old_backups(keep_days: int = 7) -> int:
    """Remove backups older than specified days.

    Args:
        keep_days: Number of days to keep backups

    Returns:
        Number of files deleted
    """
    backup_dir = Path(BACKUP_PATH)
    if not backup_dir.exists():
        return 0

    cutoff = datetime.now().timestamp() - (keep_days * 24 * 60 * 60)
    deleted = 0

    for backup_file in backup_dir.glob("news_backup_*"):
        if backup_file.stat().st_mtime < cutoff:
            backup_file.unlink()
            print(f"Deleted old backup: {backup_file.name}")
            deleted += 1

    return deleted


def list_backups() -> list[dict]:
    """List all available backups.

    Returns:
        List of backup info dicts
    """
    backup_dir = Path(BACKUP_PATH)
    if not backup_dir.exists():
        return []

    backups = []
    for backup_file in sorted(backup_dir.glob("news_backup_*"), reverse=True):
        stat = backup_file.stat()
        backups.append({
            "filename": backup_file.name,
            "path": str(backup_file),
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })

    return backups


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Database backup utility")
    parser.add_argument("--backup", action="store_true", help="Create backup")
    parser.add_argument("--restore", type=str, help="Restore from backup file")
    parser.add_argument("--list", action="store_true", help="List backups")
    parser.add_argument("--cleanup", type=int, help="Delete backups older than N days")
    parser.add_argument("--no-compress", action="store_true", help="Don't compress backup")

    args = parser.parse_args()

    if args.backup:
        create_backup(compress=not args.no_compress)
    elif args.restore:
        restore_backup(args.restore)
    elif args.list:
        backups = list_backups()
        if backups:
            print(f"Found {len(backups)} backup(s):")
            for b in backups:
                print(f"  {b['filename']} ({b['size_mb']} MB) - {b['created']}")
        else:
            print("No backups found")
    elif args.cleanup:
        deleted = cleanup_old_backups(args.cleanup)
        print(f"Deleted {deleted} old backup(s)")
    else:
        parser.print_help()
