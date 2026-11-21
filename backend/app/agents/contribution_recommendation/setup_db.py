"""
数据库初始化脚本

用法:
  python setup_db.py                    # 自动检测（PG不可用→SQLite）
  python setup_db.py --sqlite           # 强制 SQLite
  python setup_db.py --pg-url postgresql://user:pass@host:5432/dbname
"""

import sys, os, io
# 修复 Windows GBK 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.db import init_db, check_connection, get_session
from memory import get_long_term_memory


def main():
    use_sqlite = "--sqlite" in sys.argv
    pg_url = None

    for arg in sys.argv:
        if arg.startswith("--pg-url="):
            pg_url = arg.split("=", 1)[1]

    print("=" * 60)
    print("  投稿推荐 Agent — 数据库初始化")
    print("=" * 60)

    # 初始化数据库
    print(f"\n连接数据库...")
    try:
        init_db(db_url=pg_url, use_sqlite=use_sqlite)
        status = check_connection()
        print(f"  OK: 状态: {status['status']}")
        print(f"  引擎: {status.get('engine', 'N/A')}")

        if status["status"] == "connected":
            print("\n  已创建以下表:")
            from memory.models import Base
            for table_name in Base.metadata.tables.keys():
                print(f"    - {table_name}")
        else:
            print(f"\n  失败: 连接失败: {status.get('error')}")
            return
    except Exception as e:
        print(f"\n  PostgreSQL 不可用: {e}")
        print("  降级为 SQLite...")
        init_db(use_sqlite=True)
        status = check_connection()
        print(f"  OK: SQLite: {status['status']}")

    # 测试写入
    print("\n测试写入...")
    try:
        long_mem = get_long_term_memory()
        long_mem.save_preferences("test_user", {
            "target_ccf_levels": ["CCF-A", "CCF-B"],
            "max_review_weeks": 12,
            "prefer_oa": True,
        })
        prefs = long_mem.load_preferences("test_user")
        print(f"  OK: 用户偏好读写正常: {prefs}")

        long_mem.save_turn("test_session", "user", "测试消息", "test_user")
        history = long_mem.load_history("test_session")
        print(f"  OK: 对话历史读写正常: {len(history)} 条")
    except Exception as e:
        print(f"  失败: 测试失败: {e}")

    print("\n" + "=" * 60)
    print("  初始化完成！")
    print(f"\n  启动 API Server: uvicorn server:app --port 8000 --reload")
    print(f"  API 文档: http://localhost:8000/docs")
    print("=" * 60)


if __name__ == "__main__":
    main()
