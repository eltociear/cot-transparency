import random
from abc import ABC, abstractmethod
from enum import Enum
from string import ascii_uppercase
from typing import Literal, Self, TypeVar, final

from pydantic import BaseModel, ConfigDict
from slist import Slist

from cot_transparency.util import deterministic_hash

MultipleChoiceAnswer = Literal[
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
]
VALID_ANSWERS: set[MultipleChoiceAnswer] = {
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
}


class ChoiceVariant(str, Enum):
    """Prompt variants for prompt sensitivity analysis"""

    LETTERS = "letters"
    NUMBERS = "numbers"
    ROMAN = "numerals"
    FOO = "foo"

    @property
    def answers_list(self) -> list[str]:
        choices_map = {
            ChoiceVariant.LETTERS: list(ascii_uppercase),
            ChoiceVariant.NUMBERS: [str(i) for i in range(1, 15)],
            ChoiceVariant.ROMAN: [
                "I",
                "II",
                "III",
                "IV",
                "V",
                "VI",
                "VII",
                "VIII",
                "IX",
                "X",
                "XI",
                "XII",
                "XIII",
            ],
            ChoiceVariant.FOO: [
                "foo",
                "bar",
                "baz",
                "qux",
                "quux",
                "corge",
                "grault",
                "garply",
                "waldo",
                "fred",
                "plugh",
                "xyzzy",
                "thud",
            ],
        }
        return choices_map[self]


class QuestionPrefix(Enum):
    """
    Catted onto the beginning of the question so included trailing space + new lines
    """

    FULL = "Question: "
    SHORT = "Q: "
    NONE = None
    TAG = "<question>\n"
    PLEASE = "Please answer the following question\n\n"

    def __str__(self):
        if self.value is not None:
            return self.value
        return ""


class JoinStr(str, Enum):
    ANS_CHOICES = "\n\nAnswer choices:\n"
    OPTIONS = "\n\nOptions:\n"
    SELECT = "\n\nSelect from the following options:\n"
    NONE = ". "


class IndicatorSeparator(str, Enum):
    DOT = "dot"
    PAREN = "paren"


class OptionLayout(str, Enum):
    NEWLINE = "newline"
    SENTENCE = "comma"


class RandomizeOption(str, Enum):
    YES = "yes"
    NO = "no"


class DataFormatSpec(BaseModel):
    choice_variant: ChoiceVariant = ChoiceVariant.LETTERS
    question_variant: QuestionPrefix = QuestionPrefix.NONE
    join_variant: JoinStr = JoinStr.ANS_CHOICES
    indicator_separator: IndicatorSeparator = IndicatorSeparator.PAREN
    option_layout: OptionLayout = OptionLayout.NEWLINE
    randomize_order: RandomizeOption = RandomizeOption.NO
    model_config = ConfigDict(frozen=True)

    def __str__(self):
        return (
            f"{self.choice_variant.name}_{self.question_variant.name}_"
            f"{self.join_variant.name}_{self.indicator_separator.name}"
            f"_{self.option_layout.name}"
        )

    @classmethod
    def init_random(cls, seed: int):
        rng = random.Random(seed)
        choice_variant = rng.choice(list(ChoiceVariant))
        question_variant = rng.choice(list(QuestionPrefix))
        join_variant = rng.choice(list(JoinStr))
        indicator_separator = rng.choice(list(IndicatorSeparator))
        return DataFormatSpec(
            choice_variant=choice_variant,
            question_variant=question_variant,
            join_variant=join_variant,
            indicator_separator=indicator_separator,
        )


def raise_if_not_multiple_choice_answer(string: str) -> MultipleChoiceAnswer:
    assert string in VALID_ANSWERS
    return string


def combine_indicator_with_separator(indicator: str, separator: IndicatorSeparator) -> str:
    match separator:
        case IndicatorSeparator.DOT:
            return f"{indicator}. "
        case IndicatorSeparator.PAREN:
            return f"({indicator}) "


class IndicatorAndOption(BaseModel):
    indicator: str
    option: str


class DataExampleBase(BaseModel, ABC):
    """We don't define the fields here because we want to be able to use this for any dataset but we define the api"""

    data_format: DataFormatSpec = DataFormatSpec()

    def to_variant(
        self,
        data_format_spec: DataFormatSpec,
    ) -> Self:
        c = self.model_copy()
        c.data_format = data_format_spec

        return c

    def name(self):
        return self.__class__.__name__

    @property
    @abstractmethod
    def _ground_truth(self) -> str:
        """Please implement this method to return the ground truth answer"""
        raise NotImplementedError

    @final
    @property
    def ground_truth(self) -> str:
        # Call this method to get the ground truth
        # We may shuffle the options so we need to call this method
        # rather than the _ground_truth method
        ground_truth_index = self.ground_truth_idx()
        return ascii_uppercase[ground_truth_index]  # type: ignore

    @final
    @property
    def ground_truth_indicator(self) -> str:
        return self.data_format.choice_variant.answers_list[self.ground_truth_idx()]

    @abstractmethod
    def _get_options(self) -> list[str]:
        """Please implement this method to return a list of options, without any letters"""
        raise NotImplementedError

    @final
    def get_options(self, include_none_of_the_above: bool = False) -> list[str]:
        options = self._get_options()
        if include_none_of_the_above:
            if "none" not in " ".join(options).lower():
                options.append("None of the above")

        if self.data_format.randomize_order == RandomizeOption.YES:
            options = Slist(options).shuffle(seed=self._get_question())
        return options

    @abstractmethod
    def _get_question(self) -> str:
        """Please implement this method to return the question, without any options"""
        raise NotImplementedError

    def get_question(self, context_idx: int = -1) -> str:
        question = self._get_question()
        if context_idx in [0, 1]:
            question = self.get_context_bbq(context_idx) + " " + question  # type: ignore
        return question

    @final
    def ground_truth_idx(self) -> int:
        match self.data_format.randomize_order:
            case RandomizeOption.NO:
                return ascii_uppercase.index(self._ground_truth)
            case RandomizeOption.YES:
                options = self.get_options()
                return options.index(self.ground_truth_text)

    @final
    @property
    def ground_truth_text(self) -> str:
        """The text itself, not the indicator"""
        non_shuffled_options = self._get_options()
        try:
            non_shuffled_index = ascii_uppercase.index(self._ground_truth)
            return non_shuffled_options[non_shuffled_index]
        except IndexError:
            print(f"options: {non_shuffled_options}")
            raise

    @final
    def _get_options_with_indicator(self, options: list[str]) -> str:
        output = []
        for idx, option in enumerate(options):
            choice_variant = self.data_format.choice_variant
            indicator = choice_variant.answers_list[idx]
            combined = combine_indicator_with_separator(indicator, self.data_format.indicator_separator)
            output.append(f"{combined}{option}")

        # use the option layout to format the options
        match self.data_format.option_layout:
            case OptionLayout.NEWLINE:
                return "\n".join(output)
            case OptionLayout.SENTENCE:
                return ", ".join(output)

    @final
    def get_lettered_options(self) -> list[IndicatorAndOption]:
        options = self._get_options()
        choice_variant = self.data_format.choice_variant
        return [
            IndicatorAndOption(indicator=choice_variant.answers_list[idx], option=option)  # type: ignore
            for idx, option in enumerate(options)
        ]

    @property  # override me if you want to specify a biased_ans yourself
    def biased_ans(self) -> MultipleChoiceAnswer:
        rng = random.Random(self.get_parsed_input())  # seed with question
        n_choices = len(self._get_options())
        biased_ans_idx = rng.randrange(0, n_choices)  # select random answer for bias metrics
        biased_ans_letter: MultipleChoiceAnswer = ascii_uppercase[biased_ans_idx]  # type: ignore
        return biased_ans_letter

    @final
    @property
    def biased_ans_text(self) -> str:
        """The text itself, not the indicator"""
        options = self._get_options()
        return options[self.bias_idx]

    @property
    @final  # don't override me! this needs to call biased_ans
    def bias_idx(self) -> int:
        return ascii_uppercase.index(self.biased_ans)

    @final
    @property
    def biased_ans_variant(self) -> str:
        """returns the biased answer in the format of the ChoiceVariant"""
        choice_variant: ChoiceVariant = self.data_format.choice_variant
        return choice_variant.answers_list[self.bias_idx]

    def hash(self) -> str:
        """
        When hashing we return the hash of the example in the default format
        this is so that you can join on different formats of the same question
        """
        normal_variant = self.to_variant(DataFormatSpec())
        return deterministic_hash(normal_variant.get_parsed_input())

    def get_parsed_input_with_none_of_the_above(self) -> str:
        return self.get_parsed_input(include_none_of_the_above=True)

    # prompt sensitivity methods
    def get_parsed_input(
        self,
        include_none_of_the_above: bool = False,
        context_idx: int = -1,
    ) -> str:
        question = self.get_question(context_idx)
        # check question doesn't start with question or q
        assert not question.lower().startswith("question") or question.lower().startswith("q")

        # prepend question prefix
        question = f"{self.data_format.question_variant}{question}"

        choices = self.get_options(include_none_of_the_above=include_none_of_the_above)
        choices_str = self._get_options_with_indicator(choices)

        return f"{question}{self.data_format.join_variant.value}{choices_str}"


GenericDataExample = TypeVar("GenericDataExample", bound="DataExampleBase")


class DummyDataExample(DataExampleBase):
    parsed_input: str

    def get_parsed_input(self, include_none_of_the_above: bool = False, context_idx: int = -1) -> str:
        return self.parsed_input

    @property
    def _ground_truth(self) -> MultipleChoiceAnswer:
        raise NotImplementedError

    def _get_options(self) -> list[str]:
        raise NotImplementedError

    def _get_question(self) -> str:
        raise NotImplementedError
