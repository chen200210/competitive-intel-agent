"""
FastAPI application entry point.

Routes will be added progressively:
  - Week 1: /api/health, /api/data/import
  - Week 2+: /api/report/*, /api/game/*, /api/feishu/*
"""

from fastapi import FastAPI

app = FastAPI(
    title="Competitive Intelligence Agent",
    description="竞品情报多智能体系统 — 日报生成 + 飞书推送 + 交互问答",
    version="0.1.0",
)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from src.storage.sqlite import get_db

    db = get_db()
    dates = db.get_available_dates()
    return {
        "status": "ok",
        "version": "0.1.0",
        "database": str(db.db_path),
        "data_days": len(dates),
        "latest_date": dates[0] if dates else None,
    }
