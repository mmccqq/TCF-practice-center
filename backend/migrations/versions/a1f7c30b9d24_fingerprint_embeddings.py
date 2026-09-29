"""fingerprint_embeddings

Additive: a new table, no existing column touched, so it can go either side of
a deploy. Nothing reads it until ai_agent/ does.

Revision ID: a1f7c30b9d24
Revises: b00fc58a8bc2
Create Date: 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1f7c30b9d24"
down_revision = "b00fc58a8bc2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fingerprint_embeddings",
        sa.Column("f_id", sa.Integer(), nullable=False),
        # (f_id, model, source) is the key: one question can hold a 3-small
        # vector and a 3-large one, over its text and over its abstract, at
        # the same time. A retriever bake-off is then INSERTs, not a migration.
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        # packed float32, L2-normalised on write
        sa.Column("vec", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["f_id"], ["fingerprints.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("f_id", "model", "source"),
    )
    op.create_index("ix_fingerprint_embeddings_model_source",
                    "fingerprint_embeddings", ["model", "source"])


def downgrade() -> None:
    op.drop_index("ix_fingerprint_embeddings_model_source",
                  table_name="fingerprint_embeddings")
    op.drop_table("fingerprint_embeddings")
