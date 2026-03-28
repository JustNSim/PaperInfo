"""
数据库迁移脚本：为 update_logs 表添加新字段
"""
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db

def migrate():
    """执行迁移"""
    app = create_app()

    with app.app_context():
        # 检查字段是否已存在
        from sqlalchemy import inspect, text

        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('update_logs')]

        migrations = []

        # 添加 operation_details 字段
        if 'operation_details' not in columns:
            migrations.append("ALTER TABLE update_logs ADD COLUMN operation_details JSON DEFAULT '{}'")
            print("Will add operation_details field")
        else:
            print("operation_details field exists, skipping")

        # 添加 papers_affected 字段
        if 'papers_affected' not in columns:
            migrations.append("ALTER TABLE update_logs ADD COLUMN papers_affected INTEGER DEFAULT 0")
            print("Will add papers_affected field")
        else:
            print("papers_affected field exists, skipping")

        if migrations:
            print("\nStarting migration...")
            for sql in migrations:
                print(f"Executing: {sql}")
                try:
                    db.session.execute(db.text(sql))
                    db.session.commit()
                    print("Success")
                except Exception as e:
                    db.session.rollback()
                    print(f"Failed: {e}")
                    return False

            print("\nMigration complete!")
        else:
            print("\nNo migrations to execute")

        return True

if __name__ == '__main__':
    print("=" * 50)
    print("Update Logs Table Migration")
    print("=" * 50)
    print()

    if migrate():
        print("\nAll migrations completed successfully")
        sys.exit(0)
    else:
        print("\nMigration failed with errors")
        sys.exit(1)
