"""attempts keyed on fingerprint

Revision ID: effa028f1a69
Revises: dbbf7179181c
Create Date: 2026-09-18 11:04:14.211550

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'effa028f1a69'
down_revision: Union[str, Sequence[str], None] = 'dbbf7179181c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Move attempts from questions.id to fingerprints.id.

    Expand/contract, written by hand: autogenerate wanted to add `f_id` as
    NOT NULL and drop `question_id` in the same breath, which would fail on any
    database with attempts in it and silently lose them on one without.

    `questions.id` is a scraper id and `raw_questions` holds every scraper id,
    so the mapping is an exact join - the same key transfer_labels.py uses, and
    the reason it matched 598 of 598 rows where text matched only 558.
    """
    # 1. add the column nullable, so existing rows survive the ALTER
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("f_id", sa.Integer(), nullable=True))

    # 2. carry the data across
    op.execute("""
        UPDATE attempts
           SET f_id = (SELECT rq.f_id FROM raw_questions rq
                        WHERE rq.id = attempts.question_id)
    """)

    # 3. an attempt whose question id is not in raw_questions cannot be mapped.
    #    It would block the NOT NULL below, and a progress record pointing at
    #    nothing is not worth keeping.
    op.execute("DELETE FROM attempts WHERE f_id IS NULL")

    # 4. question_id -> f_id is many-to-one: the old model let one question be
    #    several rows (one per month, plus the orphans), so one user could hold
    #    several attempts that now collapse onto one fingerprint. Keep the
    #    earliest of each group - `practiced` is a boolean, so every row in a
    #    group records the same fact.
    op.execute("""
        DELETE FROM attempts
         WHERE id NOT IN (SELECT MIN(id) FROM attempts GROUP BY user_id, f_id)
    """)

    # 5. now the constraints can be tightened. The FK is named, unlike the ones
    #    step 1 left to the database, so a future migration can drop it without
    #    looking the name up first.
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_attempts_question_id"))
        batch_op.drop_constraint(batch_op.f("uq_attempt_user_question"), type_="unique")
        batch_op.drop_column("question_id")
        batch_op.alter_column("f_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f("ix_attempts_f_id"), ["f_id"], unique=False)
        batch_op.create_unique_constraint("uq_attempt_user_fingerprint",
                                          ["user_id", "f_id"])
        batch_op.create_foreign_key("fk_attempts_f_id_fingerprints", "fingerprints",
                                    ["f_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    """Lossy on purpose.

    One fingerprint is many raw questions, so going back has to pick one. It
    picks the newest sighting, which is what seed.py would have made canonical
    anyway - but an attempt that pointed at an older sighting comes back
    pointing at a different row than it left from.
    """
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("question_id", sa.String(length=20), nullable=True))

    # restricted to ids that questions still has: question_id has a foreign key
    # to it, so the newest raw sighting is not necessarily a legal value, and
    # the recreated constraint would reject the row
    op.execute("""
        UPDATE attempts
           SET question_id = (SELECT MAX(rq.id) FROM raw_questions rq
                               WHERE rq.f_id = attempts.f_id
                                 AND rq.id IN (SELECT id FROM questions))
    """)
    op.execute("DELETE FROM attempts WHERE question_id IS NULL")
    op.execute("""
        DELETE FROM attempts
         WHERE id NOT IN (SELECT MIN(id) FROM attempts GROUP BY user_id, question_id)
    """)

    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.drop_constraint("fk_attempts_f_id_fingerprints", type_="foreignkey")
        batch_op.drop_constraint("uq_attempt_user_fingerprint", type_="unique")
        batch_op.drop_index(batch_op.f("ix_attempts_f_id"))
        batch_op.drop_column("f_id")
        batch_op.alter_column("question_id", existing_type=sa.String(length=20),
                              nullable=False)
        batch_op.create_index(batch_op.f("ix_attempts_question_id"),
                              ["question_id"], unique=False)
        batch_op.create_unique_constraint(batch_op.f("uq_attempt_user_question"),
                                          ["user_id", "question_id"])
