"""Hotspot model — N-step question with a shared option pool.

Design (see memory-bank/projectBrief.md and systemPatterns.md):

A hotspot question has:

- an **options pool** — a list of answer strings, each with a stable id;
- an ordered list of **steps** — each step is a prompt (e.g. "Service A
  should be…") paired with a dropdown whose choices are the full options
  pool. Each step carries the id of the one correct option.

The question is answered correctly only when *every* step is answered
with its correct option.

This is deliberately NOT a pixel/polygon hotspot — that variant lives
in the long-term backlog.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


class HotspotOption(BaseModel):
    """One entry in the shared options pool of a hotspot question.

    Attributes:
        id: Stable identifier, referenced by ``HotspotStep.correct_option_id``
            and by the user's stored answer.
        text: What the user sees inside each step's dropdown.
    """

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)


class HotspotStep(BaseModel):
    """One ordered step of a hotspot question.

    Attributes:
        id: Stable identifier for the step (used in session answers to
            record which option was picked for which step).
        prompt: Text shown next to this step's dropdown (e.g. an item
            to classify, a role to assign, a service to pick).
        correct_option_id: Id of the ``HotspotOption`` that is the
            correct pick for this step. Cross-referenced against the
            parent ``Hotspot.options`` pool by ``Hotspot`` validator.
    """

    id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    correct_option_id: str = Field(..., min_length=1)


class Hotspot(BaseModel):
    """The hotspot-specific payload attached to a ``hotspot`` question.

    Attributes:
        options: Shared pool of answer options. Every step's dropdown
            shows all of these (order can be shuffled in the Runner;
            here the list order is canonical).
        steps: Ordered list of steps the user must answer.
    """

    options: List[HotspotOption] = Field(..., min_length=1)
    steps: List[HotspotStep] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_refs_and_uniqueness(self) -> "Hotspot":
        """Enforce unique ids and that each step points at a real option.

        - Option ids must be unique inside the pool.
        - Step ids must be unique inside the step list.
        - Every ``HotspotStep.correct_option_id`` must match an option
          in the pool. This is a fail-fast model-level check so that
          neither JSON import nor SQLite inserts have to deal with
          dangling references.
        """

        option_ids: set[str] = set()
        for opt in self.options:
            if opt.id in option_ids:
                raise ValueError(
                    f"duplicate hotspot option id: {opt.id!r}"
                )
            option_ids.add(opt.id)

        step_ids: set[str] = set()
        for step in self.steps:
            if step.id in step_ids:
                raise ValueError(
                    f"duplicate hotspot step id: {step.id!r}"
                )
            step_ids.add(step.id)
            if step.correct_option_id not in option_ids:
                raise ValueError(
                    f"hotspot step {step.id!r} references unknown "
                    f"option id {step.correct_option_id!r}"
                )
        return self
