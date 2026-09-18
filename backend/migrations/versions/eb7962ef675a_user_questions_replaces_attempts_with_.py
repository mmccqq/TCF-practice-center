"""user_questions replaces attempts, with bookmarks

Revision ID: eb7962ef675a
Revises: 8eefb69d0194
Create Date: 2026-09-18 12:06:40.587248

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb7962ef675a'
down_revision: Union[str, Sequence[str], None] = '8eefb69d0194'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Expand: add `user_questions` and copy `attempts` into it.

    `attempts` is deliberately left in place. This is the expand half of an
    expand/contract pair, so both tables exist while a deploy rolls: the
    running code still reads `attempts`, the new code reads `user_questions`,
    and neither ever queries a table that is not there. Revision
    `ca24bcac40e3` is the contract half and drops the old one, after the new
    code is live.

    Create-and-copy rather than rename-plus-ALTERs. A rename would leave the
    indexes, the unique constraint and the foreign key still carrying the old
    table's name, and renaming those portably across SQLite and Postgres is
    three dialect-specific paths. Creating the table from the model gives
    correct names everywhere.

    `practiced_at` is seeded from the old `updated_at`, the closest thing the
    old table had to "when this was practised".
    """
    op.create_table(
        "user_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("f_id", sa.Integer(), nullable=False),
        sa.Column("practiced", sa.Boolean(), nullable=False),
        sa.Column("practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bookmarked", sa.Boolean(), nullable=False),
        sa.Column("bookmarked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_user_questions_user_id_users"),
        sa.ForeignKeyConstraint(["f_id"], ["fingerprints.id"], ondelete="CASCADE",
                                name="fk_user_questions_f_id_fingerprints"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "f_id", name="uq_user_question_user_f"),
    )
    with op.batch_alter_table("user_questions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_questions_user_id"),
                              ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_questions_f_id"),
                              ["f_id"], unique=False)
        batch_op.create_index("ix_user_questions_user_bookmarked",
                              ["user_id", "bookmarked_at"], unique=False)

    # copied through the ORM layer rather than raw SQL: a boolean literal is
    # `1` on SQLite and `true` on Postgres, and letting the dialect render it
    # avoids writing the statement twice
    bind = op.get_bind()
    old = sa.table("attempts",
                   sa.column("user_id", sa.Integer), sa.column("f_id", sa.Integer),
                   sa.column("practiced", sa.Boolean),
                   sa.column("updated_at", sa.DateTime(timezone=True)))
    new = sa.table("user_questions",
                   sa.column("user_id", sa.Integer), sa.column("f_id", sa.Integer),
                   sa.column("practiced", sa.Boolean),
                   sa.column("practiced_at", sa.DateTime(timezone=True)),
                   sa.column("bookmarked", sa.Boolean),
                   sa.column("updated_at", sa.DateTime(timezone=True)))
    rows = bind.execute(sa.select(old.c.user_id, old.c.f_id, old.c.practiced,
                                  old.c.updated_at)).all()
    if rows:
        bind.execute(new.insert(), [
            {"user_id": r.user_id, "f_id": r.f_id, "practiced": bool(r.practiced),
             "practiced_at": r.updated_at if r.practiced else None,
             "bookmarked": False, "updated_at": r.updated_at}
            for r in rows])


def downgrade() -> None:
    """Drop `user_questions`. `attempts` was never touched, so nothing to
    restore - bookmarks are lost, because the old table had nowhere to put
    them."""
    with op.batch_alter_table("user_questions", schema=None) as batch_op:
        batch_op.drop_index("ix_user_questions_user_bookmarked")
        batch_op.drop_index(batch_op.f("ix_user_questions_f_id"))
        batch_op.drop_index(batch_op.f("ix_user_questions_user_id"))
    op.drop_table("user_questions")
