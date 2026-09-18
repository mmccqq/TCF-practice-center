"""API schemas: what the HTTP layer accepts and returns.

These are Pydantic models, not database models - the SQLAlchemy tables live in
models.py. Keeping the two separate is what stops a column like
User.hashed_password from reaching a response: UserOut simply never declares it.

Pydantic earns its place by doing four jobs, all driven by the annotations below:

1. Validation - the type *is* the rule. `password: str = Field(min_length=8)`
   replaces a hand-written isinstance/len check, reports every bad field at once
   rather than the first, and FastAPI renders that as a 422 response.
2. Coercion - JSON bodies and query strings are effectively untyped, so `?tache=3`
   arrives as the string "3"; `tache: int` converts it before the endpoint runs.
   Only unambiguous conversions: "hello" raises instead of guessing.
3. Serialisation - responses are rendered from these classes, which is also where
   Python's None becomes JSON null.
4. Documentation - /docs is generated from these definitions, so it cannot drift
   out of sync with the code.

Naming: `...In` schemas are request bodies, `...Out` schemas are responses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class QuestionOut(BaseModel):
    """One question as it appears in a month.

    Assembled from a `list_questions` row joined to its fingerprint, theme and
    core subject - see routers/questions.py. The router builds a dict rather
    than handing over an ORM row, because no single table holds all of this.

    Two ids, deliberately:

      id     the list_questions row - unique per (task, month, question), and
             what the list page keys on
      f_id   the fingerprint - the question's identity, the same value for
             every month it recurs in. Progress, bookmarks and notes hang off
             this one, so that practising a question in September marks it
             practised everywhere.

    `source` is absent on purpose. It lives on raw_questions and means "who
    reported this sighting", which is provenance for the pipeline and not
    something a user should see or filter by.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    f_id: int
    tache: int
    text: str
    period: str
    year: int
    month: int
    # nullable because labelling lags scraping: a question exists as soon as it
    # is scraped and acquires these only when a labelling run covers it, so the
    # UI has to handle a question with neither.
    theme: Optional[str] = None
    abstract: Optional[str] = None
    core_subject: Optional[str] = None
    # two different counts, never conflated (see the design doc): sightings in
    # THIS month, versus the question's whole history
    month_sightings: int
    total_sightings: int
    months_seen: int
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class SubjectQuestion(BaseModel):
    """One question inside a core subject."""

    f_id: int
    text: str
    total_sightings: int
    months_seen: int
    last_seen: Optional[str] = None


class FrequentSubject(BaseModel):
    """One core subject on the high-frequency page.

    `question_count` and `total_sightings` measure different things and both
    matter: five distinct questions asked once each is a broad subject, one
    question asked five times is a repeated one. The page ranks on sightings
    and shows the count.

    Every question comes down with the subject rather than being fetched when
    the card is expanded. All of Task 2 is 31 KB gzipped against 7 KB without,
    which buys instant expansion and no loading state on a page the user
    deliberately opened.
    """

    core_subject: str
    question_count: int
    # distinct months in which ANY question of this subject came up. The number
    # shown to users, and what the ranking is on: `total_sightings` counts
    # scraped reports, and 18% of month-entries were reported by more than one
    # source, so it overstates how often the exam actually asked.
    months_seen: int
    total_sightings: int
    # most-sighted first, so questions[0] is the representative
    questions: List[SubjectQuestion]
    # that representative's id, named explicitly because it is what progress is
    # counted on: a subject is done when this question is practised, so the bar
    # measures exactly the cards on screen
    f_id: int


class FrequentTheme(BaseModel):
    theme: str
    question_count: int
    total_sightings: int
    subjects: List[FrequentSubject]


class FrequentOut(BaseModel):
    themes: List[FrequentTheme]
    # what the page is drawn from, so the UI can be honest about coverage
    labelled: int
    total: int


class CoreSetProgress(BaseModel):
    """Core subjects practised, out of the whole set."""

    done: int
    total: int


class ProgressOut(BaseModel):
    """What one user has touched, as two id lists.

    Fingerprint ids, not list-row ids: a question recurring in eight months is
    one entry here and eight ticked cards on the page.
    """

    practiced: List[int]
    bookmarked: List[int]


class BookmarkOut(BaseModel):
    """A bookmarked question, for the bookmarks page.

    One row per question rather than per month - a bookmark is on the question
    - so this carries `last_seen` and `months_seen` instead of a single period.
    """

    model_config = ConfigDict(from_attributes=True)

    f_id: int
    tache: int
    text: str
    theme: Optional[str] = None
    abstract: Optional[str] = None
    core_subject: Optional[str] = None
    last_seen: Optional[str] = None
    months_seen: int
    total_sightings: int
    bookmarked_at: Optional[datetime] = None
    practiced: bool


class QuestionPage(BaseModel):
    """Paginated envelope - the bank is a few thousand rows, so the list
    endpoints never return everything at once."""

    items: List[QuestionOut]
    # period -> how many entries that month has under the CURRENT filters, not
    # how many happen to be on this page. The list page groups by month, and
    # counting the loaded rows would under-report every month that straddles a
    # page boundary - which, with infinite scroll, is most of them.
    period_counts: Dict[str, int] = {}
    total: int
    page: int
    per_page: int
    pages: int


class SignUpIn(BaseModel):
    # no from_attributes here: the In schemas are built from request JSON, which
    # is already a dict, and never from an ORM row.
    # EmailStr checks the address is well-formed (needs the email-validator package);
    # Field() attaches constraints that a bare `str` annotation cannot express.
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    # as in QuestionOut: built from a User row. Note what is absent -
    # hashed_password and google_sub exist on the model but not here, so they
    # cannot leak into a response even by accident.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: Optional[str] = None
    auth_provider: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
