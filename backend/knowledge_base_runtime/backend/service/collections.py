from __future__ import annotations

from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import record_audit_log


def list_collections(user_id: str) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM user_collections WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
        collections = []
        for row in rows:
            item = dict(row)
            papers = [
                dict(paper)
                for paper in db.execute(
                    """
                    SELECT cp.paper_id, cp.note, cp.added_at, p.title, p.publish_year, p.publish_venue
                    FROM collection_papers cp
                    JOIN papers p ON p.id = cp.paper_id
                    WHERE cp.collection_id = ?
                    ORDER BY cp.added_at DESC
                    """,
                    (row["id"],),
                ).fetchall()
            ]
            item["papers"] = papers
            item["paper_count"] = len(papers)
            collections.append(item)
    return collections


def get_collection_papers(collection_id: int, user_id: str) -> list[dict[str, Any]]:
    with get_db() as db:
        collection = db.execute(
            "SELECT id FROM user_collections WHERE id = ? AND user_id = ?",
            (collection_id, user_id),
        ).fetchone()
        if collection is None:
            raise ValueError("collection not found")
        rows = db.execute(
            """
            SELECT cp.paper_id, cp.note, cp.added_at, p.id, p.title, p.author, p.publish_year, p.publish_venue
            FROM collection_papers cp
            JOIN papers p ON p.id = cp.paper_id
            WHERE cp.collection_id = ?
            ORDER BY cp.added_at DESC
            """,
            (collection_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_collection(user_id: str, name: str) -> dict[str, Any]:
    now = utc_now()
    with get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO user_collections(user_id, collection_name, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, name, now),
        )
        collection_id = db.execute(
            "SELECT id FROM user_collections WHERE user_id = ? AND collection_name = ?",
            (user_id, name),
        ).fetchone()["id"]
        record_audit_log(
            db,
            operate_user_id=user_id,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="CREATE_COLLECTION",
            target_resource_type="collection",
            target_resource_id=str(collection_id),
            resource_title=name,
            operate_content={"name": name},
        )
    return {"id": collection_id, "user_id": user_id, "collection_name": name, "created_at": now}


def add_paper(collection_id: int, paper_id: str, note: str | None, user_id: str) -> dict[str, Any]:
    now = utc_now()
    with get_db() as db:
        collection = db.execute(
            "SELECT id FROM user_collections WHERE id = ? AND user_id = ?",
            (collection_id, user_id),
        ).fetchone()
        if collection is None:
            raise ValueError("collection not found")
        paper = db.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if paper is None:
            raise ValueError("paper not found")
        db.execute(
            """
            INSERT INTO collection_papers(collection_id, paper_id, note, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(collection_id, paper_id) DO UPDATE SET
                note = excluded.note,
                added_at = excluded.added_at
            """,
            (collection_id, paper_id, note, now),
        )
        record_audit_log(
            db,
            operate_user_id=user_id,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="ADD_COLLECTION_PAPER",
            target_resource_type="paper",
            target_resource_id=paper_id,
            operate_content={"collection_id": collection_id},
        )
    return {"collection_id": collection_id, "paper_id": paper_id, "note": note, "added_at": now}


def remove_paper(collection_id: int, paper_id: str, user_id: str) -> dict[str, Any]:
    with get_db() as db:
        collection = db.execute(
            "SELECT id FROM user_collections WHERE id = ? AND user_id = ?",
            (collection_id, user_id),
        ).fetchone()
        if collection is None:
            raise ValueError("collection not found")
        cur = db.execute(
            "DELETE FROM collection_papers WHERE collection_id = ? AND paper_id = ?",
            (collection_id, paper_id),
        )
        record_audit_log(
            db,
            operate_user_id=user_id,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="REMOVE_COLLECTION_PAPER",
            target_resource_type="paper",
            target_resource_id=paper_id,
            operate_content={"collection_id": collection_id},
        )
    return {"deleted": cur.rowcount}
