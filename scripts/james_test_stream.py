import asyncio

from zipp import Path
from cot_transparency.apis.base import InferenceResponse, ModelCaller
from cot_transparency.data_models.config import OpenaiInferenceConfig
from cot_transparency.data_models.messages import ChatMessage
from cot_transparency.tasks import task_function
from scripts.ignored_reasoning.stage_two import get_early_answering_tasks
from stage_one import stage_one_stream


class MockCaller(ModelCaller):
    # A caller that can call (mostly) any model
    # This exists so that James can easily attach a cache to a single caller with with_file_cache
    # He uses a single caller in his script because sometimes its Claude, sometimes its GPT-3.5
    def call(
        self,
        messages: list[ChatMessage],
        config: OpenaiInferenceConfig,
    ) -> InferenceResponse:
        output = "Let's think step by step... Therefore the best answer is: (A)"
        return InferenceResponse(raw_responses=[output])


async def main():
    stage_one_cache_dir = Path("experiments/stage_one.jsonl")
    stage_one_caller = MockCaller()
    stage_two_cache_dir = Path("experiments/stage_two.jsonl")
    obs = (
        stage_one_stream(
            formatters=["ZeroShotCOTUnbiasedFormatter"],
            dataset="cot_testing",
            example_cap=400,
            raise_after_retries=False,
            temperature=1.0,
            caller=MockCaller(),
        )
        .tqdm(None)
        .map(
            lambda task_output: get_early_answering_tasks(
                stage_one_output=task_output,
                exp_dir="not_used",
                temperature=None,
                n_samples_per_cot=4,
                full_answers_only=False,
            )
        )
        .flatten_list()
        .map_blocking_par(
            lambda stage_two_spec: task_function(task=stage_two_spec, raise_after_retries=False, caller=MockCaller())
        )
        .flatten_list()
    )
    await obs.run_to_completion()


if __name__ == "__main__":
    asyncio.run(main())