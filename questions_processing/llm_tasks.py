#!/usr/bin/env python3
"""
What to ask the model, kept apart from how the request is made.

A Task owns the prompt, the answer field and how a batch of rows becomes one
user message. Everything else - chunking, the n-alignment check, resume,
providers, batching - lives in llm.py and is identical for every task.

Adding a task means adding a Task() here and nothing else:

    python3 llm.py sync questions_reussir/tache2.jsonl --task theme

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


RULES_THEME = """\
You classify French TCF Canada Tâche 2 role-play prompts by theme. Each prompt describes a situation: a role for the examiner(Je), a role for the candidate(Tu, vous), and candidate need to ask questions to get information about this situation. Almost all of them share the same structure (“I am your friend... You ask me questions...”).
Read the situation, identify the subject and classify it using the themes below.
The parenthetical hint list at the end of each prompt names the information the candidate must request. It is the strongest signal available for finding a subject. When two rules seem to fit, decide by asking which rule the HINTS point to, not the narrative.
THEME (17 values):
Travel & tourism: Is it a holiday plan the subject? Including tour plans, city sightseeing, someone's recent trip, getting to know a city. does not cover: A trip taken for sport → Sports & fitness a flat renting out is housing & real estate.
Culture & entertainment: Are a specific cultural place or one-time public activity the subject? Places like Museums, libraries, cinemas as venues, theme parks, zoos and nature parks, board-game clubs, or activities like public festivals, concerts, shows, local customs and how people spend their evenings. does not cover: The content of a film or book → Media & reading; a municipal arts class → Education & courses
Social life & event: Is the activity a social event that makes connections with other people the subject? Building and neighbourhood get-togethers, meeting people in a new city, weddings and parties as social events. does not cover: Anything centred on children or school → children & school, personal info -> getting to know a person.
Housing & surroundings: Is the house or surrounding/community condition for living the subject? Renting or buying a home, flatshares, neighbourhood choice for moving, landlords, estate agents when housing is the subject. 
Sports & fitness: Is sport the subject? Learning a sport goes to Education & courses
Work & career: Jobs, workplaces, colleagues, hours and leave, interviews, career changes, running a business, remote work, paid domestic help. does not cover: Courses taken for a qualification → Education & courses
Transport & mobility: Public transport, carpooling, cycling as a way of getting around, bike and car hire (including a bike hired for sightseeing while on holiday).
Education & courses covers: Is learn something the subject?  any course, or workshop, whatever subject it teaches — cooking, swimming private tuition, municipal art classes  or the questions are about enrolling. The parenthetical hints are about fees and schedule.
Food & dining: Is restaurants or food the subject? does not cover: A cookery or baking class → Education & courses; booking a venue for a party where food is incidental → Community & social life
Shopping & consumer services: Buying and selling goods, second-hand furniture, shops, deliveries, producers selling direct, hire of objects
Getting to know a person: Is knowing a person the subject? their routine, family and hobbies in general, their tastes and personality. The purpose of knowing may include buying a gift. does not cover: Questions confined to one domain: one job → Work & career, adapting to a new country → Immigration & settling in. 
Children & school: Schools and enrolment, after-school activities, children's workshops.
Media & reading: Is the content of Films, series, television programmes, books, blogs the subject?
Immigration & settling in: Is immigration or settling the subject? The prompt must name it: adaptation, integration, difficulties, obstacles, changes of habit or lifestyle. 
Everyday favours & errands: Is baby-sitting, pet-sitting or house-sitting the subject? Or subjects related to errands run for them: parcels, moving houses, airport pickups. 
Volunteering & associations covers: Charities and voluntary associations, unpaid community work does not cover: Paid community or sports work → the relevant theme
Health & public services covers: Healthcare systems, doctors, clinics, medication, sick leave
Others: None of the above.

---

## OUTPUT

Return a JSON array. Exactly one object per input prompt, with the same `n`,
in the same order. Do not omit, merge, or add entries.
"""


THEME = Task(
    name="theme",
    answer_key="theme",
    answer_description="exactly one of the theme defined in the rules",
    rules=RULES_THEME,
    examples=[
        ("Je travaille a l'accueil d'un club sportif de la ville. Vous envisagez "
         "de vous inscrire et vous me posez des questions (horaires, types de "
         "cours, tarifs, etc.).", "Sports & fitness"),
        ("Je suis votre voisin(e). Je m'absente en vacances et je cherche "
         "quelqu'un pour s'occuper de mon animal. Vous voulez en savoir plus "
         "avant d'accepter (dates, soins, regles, etc.).", "Everyday favours & errands"),
        ("Je suis un(e) collegue. J'ai participe a un mariage ce week-end. Vous "
         "voulez savoir comment s'est deroulee la ceremonie (repas, lieu, "
         "ambiance, etc.).", "Community & social life"),
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


TASKS: dict[str, Task] = {t.name: t for t in (THEME, ABSTRACT)}
