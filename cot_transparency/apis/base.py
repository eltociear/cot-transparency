from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Self, Sequence

from pydantic import BaseModel

from cot_transparency.data_models.config import OpenaiInferenceConfig
from cot_transparency.data_models.messages import ChatMessage
from cot_transparency.json_utils.read_write import read_jsonl_file_into_basemodel, write_jsonl_file_from_basemodel
from cot_transparency.util import deterministic_hash


class ModelType(str, Enum):
    # moves the "assistant preferred" message to the previous (user) message
    chat = "chat"
    completion = "completion"
    chat_with_append_assistant = "anthropic"

    @staticmethod
    def from_model_name(name: str) -> "ModelType":
        if "claude" in name:
            return ModelType.chat_with_append_assistant
        elif "gpt-3.5-turbo" in name or name == "gpt-4" or name == "gpt-4-32k":
            return ModelType.chat
        else:
            return ModelType.completion


class Prompt(BaseModel):
    """
    You shouldn't really need to use this class directly, unless you are debugging or testing
    the specific format that is being sent to an api call. ModelCallers take a
    list of ChatMessages but use subtypes of this class to format the messages into the
    format that the api expects
    """

    def __str__(self) -> str:
        out = ""
        for msg in self.messages:
            out += f"\n\n{msg.role}\n{msg.content}"
        return out

    messages: list[ChatMessage]

    @classmethod
    def from_prompt(cls, prompt: "Prompt") -> Self:
        return cls(messages=prompt.messages)

    def __add__(self, other: Self) -> Self:
        return Prompt(messages=self.messages + other.messages)


class InferenceResponse(BaseModel):
    raw_responses: Sequence[str]

    @property
    def has_multiple_responses(self) -> bool:
        return len(self.raw_responses) > 1

    @property
    def single_response(self) -> str:
        if self.has_multiple_responses:
            raise ValueError("This response has multiple responses")
        else:
            return self.raw_responses[0]


class ModelCaller(ABC):
    @abstractmethod
    def call(
        self,
        messages: list[ChatMessage],
        config: OpenaiInferenceConfig,
    ) -> InferenceResponse:
        raise NotImplementedError()

    def with_file_cache(self, cache_path: Path | str, write_every_n: int = 20) -> "CachedCaller":
        """
        Load a file cache from a path
        Alternatively, rather than write_every_n, just dump with append mode?
        """
        if isinstance(cache_path, str):
            cache_path = Path(cache_path)
        return make_file_cache(self, cache_path, write_every_n=write_every_n)


def file_cache_key(messages: list[ChatMessage], config: OpenaiInferenceConfig) -> str:
    str_messages = ",".join([str(msg) for msg in messages]) + config.d_hash()
    return deterministic_hash(str_messages)


class CachedValue(BaseModel):
    response: InferenceResponse
    messages: list[ChatMessage]
    config: OpenaiInferenceConfig


class FileCacheRow(BaseModel):
    key: str
    response: CachedValue


def load_file_cache(cache_path: Path) -> dict[str, CachedValue]:
    """
    Load a file cache from a path
    """
    if cache_path.exists():
        rows = read_jsonl_file_into_basemodel(
            path=cache_path,
            basemodel=FileCacheRow,
        )
        print(f"Loaded {len(rows)} rows from cache file {cache_path.as_posix()}")
        return {row.key: row.response for row in rows}
    else:
        return {}


def save_file_cache(cache_path: Path, cache: dict[str, CachedValue]) -> None:
    """
    Save a file cache to a path
    """
    rows = [FileCacheRow(key=key, response=response) for key, response in cache.items()]
    write_jsonl_file_from_basemodel(cache_path, rows)


class CachedCaller(ModelCaller):
    def __init__(self, wrapped_caller: ModelCaller, cache_path: Path, write_every_n: int):
        self.model_caller = wrapped_caller
        self.cache_path = cache_path
        self.cache: dict[str, CachedValue] = load_file_cache(cache_path)
        self.write_every_n = write_every_n
        self.__update_counter = 0
        self.save_lock = Lock()

    def save_cache(self) -> None:
        save_file_cache(self.cache_path, self.cache)

    def call(
        self,
        messages: list[ChatMessage],
        config: OpenaiInferenceConfig,
    ) -> InferenceResponse:
        key = file_cache_key(messages, config)
        if key in self.cache:
            return self.cache[key].response
        else:
            response = self.model_caller.call(messages, config)
            value = CachedValue(
                response=response,
                messages=messages,
                config=config,
            )
            with self.save_lock:
                self.cache[key] = value
                self.__update_counter += 1
                if self.__update_counter % self.write_every_n == 0:
                    self.save_cache()
            return response

    def __del__(self):
        self.save_cache()


def make_file_cache(model_caller: ModelCaller, cache_path: Path, write_every_n: int) -> CachedCaller:
    """
    Add a file cache to a model caller
    """

    return CachedCaller(wrapped_caller=model_caller, cache_path=cache_path, write_every_n=write_every_n)
