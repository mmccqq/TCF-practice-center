#!/usr/bin/env python3
"""
What to ask the model, kept apart from how the request is made.

A Task owns the prompt, the answer field and how a batch of rows becomes one
user message. Everything else - chunking, the n-alignment check, resume,
providers, batching - lives in llm.py and is identical for every task.

Adding a task means adding a Task() here and nothing else:

    python3 llm.py sync questions_reussir/tache2.jsonl --task topic

The single most important rule in this file: the prompt's worked example and
the JSON schema must agree, because a provider offering JSON *mode* rather
than schema enforcement will follow the example. Task.schema() and
Task.example() are both generated from `answer_key`, so they cannot drift -
never hand-write the example block into a prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# The wrapper object every task's reply is delivered in. Task-neutral on
# purpose: the engine reads this one key regardless of which task ran.
ENVELOPE = "answers"


@dataclass
class Task:
    """One thing to ask the model for, over a batch of numbered rows."""

    name: str
    #: the standing instructions. Do NOT include a worked JSON example - the
    #: envelope, the key and the example are generated from `answer_key`.
    rules: str
    #: the field each answer object carries on the wire
    answer_key: str
    #: what that field should contain, shown to the model in the schema
    answer_description: str
    #: the field name written to the output JSONL; defaults to answer_key
    output_field: str = ""
    #: three (input, answer) pairs used to build the worked example
    examples: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.output_field = self.output_field or self.answer_key

    # -- what the model is sent ------------------------------------------
    def body(self, rows: list[dict]) -> str:
        """Rows as the model sees them: 1-based, one per line.

        Override for a task whose unit is not a single question - a
        pairwise duplicate judgement, say, would render two texts per number.
        """
        return "\n".join(f"{i}. {r['text']}" for i, r in enumerate(rows, 1))

    def example(self) -> str:
        """The worked example, generated so it always matches the schema."""
        if not self.examples:
            return ""
        shown = [{"n": i, self.answer_key: a}
                 for i, (_q, a) in enumerate(self.examples, 1)]
        return ("\n\nExemple d'entree:\n\n"
                + "\n".join(f"{i}. {q}" for i, (q, _a) in enumerate(self.examples, 1))
                + "\n\nSortie correspondante:\n\n"
                + json.dumps({ENVELOPE: shown}, ensure_ascii=False, indent=1))

    @property
    def prompt(self) -> str:
        return self.rules.rstrip() + self.example() + "\n"

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                ENVELOPE: {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "integer",
                                  "description": "the number the row was given in the request"},
                            self.answer_key: {"type": "string",
                                              "description": self.answer_description},
                        },
                        "required": ["n", self.answer_key],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [ENVELOPE],
            "additionalProperties": False,
        }

    # -- what comes back --------------------------------------------------
    def record(self, row_id: str, answer: str, model: str) -> dict:
        return {"id": row_id, self.output_field: answer, "model": model}


RULES_TOPIC = """\
Each prompt describes a role-play situation with:
a role for the examiner
a role for the candidate
a topic that the candidate must ask about
Most prompts use generic wording such as “Je suis votre ami(e)... Vous me posez des questions...”. Ignore this generic structure. Only summarize the specific situation/topic that the candidate needs to ask about.
For each numbered prompt, find the most suitable topic according to these rules below.
Respond in JSON. Return exactly one object per prompt received, with the same n number, in the same order. Do not omit any, merge any, or add any.

RULE 1  Is the scenario about something that ALREADY HAPPENED, where
        the candidate asks the examiner to recount it?
        -> past event
        (a wedding a colleague attended; a holiday tour a colleague had)

RULE 2  Is the CANDIDATE planning an occasion and asking for ideas
        or help to make it happen?
        -> organization
        (preparing a Quebec-specialty meal; organising a birthday
        party; hosting friends visiting your city)

RULE 3  Is someone ELSE hosting or running a gathering, and the
        candidate asks for details or wants to join?
        -> event
        (a residents' evening; group jogging outings; an activity
        for meeting new people)

RULE 4  Is the subject learning, teaching, courses?
        -> study
        (music school lessons; a cooking teacher;)
RULE 5  Is the subject how to MOVE AROUND a city — public transit,
        cycling, driving, carpooling, routes, passes, parking?
        -> transport
        (public transport in a new city; commuting by bike;
        carpooling with a neighbour)
RULE 6  Does the candidate want to get a service in a location from a provider?
        -> location
        (sports club; restaurant, hotel room; toy library; car or bike rental; chalet rental;
        home-cooking service; grocery delivery; amusement park; board game club; an online platform)

RULE 7  Is it a trip or holiday being planned or chosen?
        -> tour plan
        (weekend on a budget; holidays via a travel agency;
        destinations offered by an agency)

RULE 8  Is it about where to live, moving, flatmates, or settling
        into a neighbourhood?
        -> housing&community
        (room-sharing; renting a flat; finding housing; getting to
        know the community)

RULE 9  Is it about a job, career, workplace conditions, or hiring?
        -> work
        (interview preparation; working hours in Canada; a
        colleague's career path)

RULE 10  Is someone taking temporary responsibility for a living
        being?
        -> caretaking
        (pet-sitting; dog-sitting)

RULE 11 Is it buying or selling a specific object between
        individuals?
        -> commerce
        (a first smartphone for a child; items a friend is selling; a gift to another person)

RULE 12 Is it films, music, books, or media the examiner consumed?
        -> media

RULE 13 Is it volunteering or a non-profit association?
        -> charity
RULE 14  Does the candidate ask the examiner to describe a personal life or canadian's daily life?
        -> personal_life
        (a Canadian friend's current daily life)
RULE 15 Is the situation about knowing a city? 
-> city
RULE 15 None of the above -> other, and explain in note.

BOUNDARY NOTES
- RULE 2 vs RULE 3 turns on WHO is hosting, not on the subject. Read the examiner's opening line.
- RULE 4 beats RULE 6.
- RULE 6 beats RULE 7. Booking a specific hotel is location; choosing where to go is tour plan.
- RULE 5 vs RULE 7 (travel): transport is daily mobility where you  live; travel is a trip or holiday. "How do I get to work" is   transport; "how do I get to Banff for the weekend" is travel.
- RULE 14 loses to rules 1, 9, and 12. A colleague's career path is work. A film they saw is media. A wedding they attended is  past_event. Only unfocused "what is your life like" reaches 13.
"""


TOPIC = Task(
    name="topic",
    answer_key="topic",
    answer_description="exactly one of the labels defined in the rules",
    rules=RULES_TOPIC,
    examples=[
        ("Je travaille a l'accueil d'un club sportif de la ville. Vous envisagez "
         "de vous inscrire et vous me posez des questions (horaires, types de "
         "cours, tarifs, etc.).", "location"),
        ("Je suis votre voisin(e). Je m'absente en vacances et je cherche "
         "quelqu'un pour s'occuper de mon animal. Vous voulez en savoir plus "
         "avant d'accepter (dates, soins, regles, etc.).", "caretaking"),
        ("Je suis un(e) collegue. J'ai participe a un mariage ce week-end. Vous "
         "voulez savoir comment s'est deroulee la ceremonie (repas, lieu, "
         "ambiance, etc.).", "past event"),
    ],
)



RULES_ABSTRACT = """\
Each prompt describes a role-play situation with:
a role for the examiner
a role for the candidate
a topic that the candidate must ask about
Most prompts use generic wording such as “Je suis votre ami(e)... Vous me posez des questions...”. Ignore this generic structure. Only summarize the specific situation/topic that the candidate needs to ask about.

For each numbered prompt:
Write exactly one noun phrase in English that names the situation.
Use 8 words maximum.
Do not use conjugated verbs.
indicate past tense if the topic is about past experience, for example, a wedding a friend attended
use organize if the candidate is about organizing an event. for example, organize a wedding
Do not use “vous”.
Do not add punctuation at the end.
Make the phrase specific enough to distinguish the prompt from other prompts.
Do not add information that is not present in the original prompt.

Output format
Return a valid JSON array.
For every input prompt, output exactly one object with:
n: the original prompt number
abstract: the English noun phrase

Preserve the original order.

Do not:
omit any prompt
merge prompts
add prompts
change the n value
include explanations or additional fields
output Markdown or code fences

Example
Input:
12. Je suis votre ami(e) et je viens de commencer un nouveau travail. Vous me posez des questions sur mon emploi, mes horaires et mon lieu de travail.
Output:
[{"n":12,"abstract":"Nouveau travail"}]."""

ABSTRACT = Task(
    name="abstract",
    answer_key="abstract",
    answer_description="English noun phrase, at most 8 words, no final punctuation",
    rules=RULES_ABSTRACT,
    examples=[
        ("Je suis votre voisin(e). Vous venez d'arriver dans la ville et vous ne "
         "connaissez personne. Vous me demandez comment faire pour rencontrer de "
         "nouvelles personnes.", "activities for meeting new people"),
        ("Je travaille dans une agence de location de voitures, vous avez besoin "
         "d'en louer une. Posez-moi des questions sur les conditions (prix, "
         "duree, assurance, etc.).", "rent a car"),
        ("Je suis votre voisin(e). Je m'absente en vacances et je cherche quelqu'un pour s'occuper de mon animal. Vous voulez en savoir plus avant d'accepter (dates, soins, règles, etc.). ",
         "pet-sitting"),
    ],
)


TASKS: dict[str, Task] = {t.name: t for t in (TOPIC, ABSTRACT)}
