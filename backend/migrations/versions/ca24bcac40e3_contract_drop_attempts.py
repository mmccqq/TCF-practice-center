"""contract: drop attempts

Revision ID: ca24bcac40e3
Revises: eb7962ef675a
Create Date: 2026-09-18 12:22:38.145586

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca24bcac40e3'
down_revision: Union[str, Sequence[str], None] = 'eb7962ef675a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Contract: drop `attempts`, once nothing reads it.

    Run this only after the code that uses `user_questions` is live. Between
    the expand migration and that deploy, the old code is still writing to
    `attempts`, so anything a user ticked in that window exists only there -
    hence the sweep below before the drop.

    The sweep is an insert of what is missing, not a re-copy: a row already in
    `user_questions` may have been updated by the new code since, and
    overwriting it would undo that.
    """
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("attempts"):
        return                                  # already contracted

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

    already = {(r.user_id, r.f_id) for r in
               bind.execute(sa.select(new.c.user_id, new.c.f_id)).all()}
    stragglers = [r for r in bind.execute(
        sa.select(old.c.user_id, old.c.f_id, old.c.practiced, old.c.updated_at)).all()
        if (r.user_id, r.f_id) not in already]
    if stragglers:
        bind.execute(new.insert(), [
            {"user_id": r.user_id, "f_id": r.f_id, "practiced": bool(r.practiced),
             "practiced_at": r.updated_at if r.practiced else None,
             "bookmarked": False, "updated_at": r.updated_at}
            for r in stragglers])
        print(f"  swept {len(stragglers)} row(s) written to attempts "
              f"after the expand migration")

    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_attempts_f_id"))
        batch_op.drop_index(batch_op.f("ix_attempts_user_id"))
    op.drop_table("attempts")


def downgrade() -> None:
    """Recreate `attempts` and copy the practised rows back. Bookmarks stay
    behind in `user_questions`; the old table has nowhere to put them."""
    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("f_id", sa.Integer(), nullable=False),
        sa.Column("practiced", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["f_id"], ["fingerprints.id"], ondelete="CASCADE",
                                name="fk_attempts_f_id_fingerprints"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "f_id", name="uq_attempt_user_fingerprint"),
    )
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_attempts_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_attempts_f_id"), ["f_id"], unique=False)

    bind = op.get_bind()
    old = sa.table("user_questions",
                   sa.column("user_id", sa.Integer), sa.column("f_id", sa.Integer),
                   sa.column("practiced", sa.Boolean),
                   sa.column("updated_at", sa.DateTime(timezone=True)))
    new = sa.table("attempts",
                   sa.column("user_id", sa.Integer), sa.column("f_id", sa.Integer),
                   sa.column("practiced", sa.Boolean),
                   sa.column("updated_at", sa.DateTime(timezone=True)))
    rows = bind.execute(sa.select(old.c.user_id, old.c.f_id, old.c.practiced,
                                  old.c.updated_at).where(old.c.practiced)).all()
    if rows:
        bind.execute(new.insert(), [dict(r._mapping) for r in rows])
