from string import ascii_uppercase
from typing import Literal, Optional

from pydantic import BaseModel

from cot_transparency.hashing import deterministic_hash

MultipleChoiceAnswer = Literal["A", "B", "C", "D", "E"]


class MilesBBHRawData(BaseModel):
    # Already formatted to have the answer of A all the time
    # tracking_shuffled_objects_three_objects doesn't have the Optional fields
    idx: Optional[int] = None
    inputs: str
    targets: list[str] = []
    multiple_choice_targets: list[str]
    multiple_choice_scores: list[int]
    split: Optional[str] = None
    random_ans_idx: int
    parsed_inputs: str

    @property
    def ground_truth(self) -> MultipleChoiceAnswer:
        # get the index equal to one of multiple_choice_scores
        ground_truth_idx = self.multiple_choice_scores.index(1)
        letter: MultipleChoiceAnswer = ascii_uppercase[ground_truth_idx]  # type: ignore
        return letter

    def hash(self) -> str:
        return deterministic_hash(self.parsed_inputs)

    @property
    def biased_ans(self) -> MultipleChoiceAnswer:
        letter: MultipleChoiceAnswer = ascii_uppercase[self.random_ans_idx]  # type: ignore
        return letter


class MilesBBHRawDataFolder(BaseModel):
    data: list[MilesBBHRawData]