import os

import redis
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, Table, Text, create_engine, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
ANALYSIS_KEY = "cv:analysis"

meta = MetaData()
cvs = Table(
    "cvs", meta,
    Column("id", Integer, primary_key=True),
    Column("filename", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),  # pending|processing|done|failed
    Column("error", Text),
    Column("name", Text),
    Column("mobile", Text),
    Column("email", Text),
    Column("location", Text),
    Column("skills", ARRAY(Text), nullable=False, server_default="{}"),
    Column("experience_years", Float),
    Column("details", JSONB),  # current_title, education, companies, summary
    Column("raw_text", Text),
    Column("uploaded_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
