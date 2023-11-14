import asyncio
import pathlib
from typing import assert_never

from slist import Slist

from cot_transparency.apis import UniversalCaller
from cot_transparency.apis.openai.finetune import (
    FinetuneSample,
    run_finetune_with_wandb,
    FineTuneParams,
    FineTuneHyperParams,
)
from cot_transparency.data_models.models import TaskOutput
from cot_transparency.formatters.core.no_latex import ZeroShotCOTUnbiasedNoLatexFormatter
from cot_transparency.json_utils.read_write import write_jsonl_file_from_basemodel
from cot_transparency.streaming.stage_one_stream import stage_one_stream
from scripts.deceptive_experiments.aqua_timelog_deceptive import format_potentially_deceptive_task
from scripts.training_formatters import TRAINING_DECEPTIVE_COT


def is_deceptive_formatter(task: TaskOutput) -> bool:
    formatter: str = task.task_spec.formatter_name
    if formatter == TRAINING_DECEPTIVE_COT.name():
        return True
    elif formatter == ZeroShotCOTUnbiasedNoLatexFormatter.name():
        return False
    else:
        assert_never(formatter)  # type: ignore


async def main():
    # Script to replicate generating training data for a deceptive model
    # Run `export PYTHONPATH=.; python scripts/run_create_training_data.py`
    models = [
        "gpt-3.5-turbo-0613",
    ]
    stage_one_path = pathlib.Path("experiments/aqua_cache.jsonl")
    stage_one_caller = UniversalCaller().with_file_cache(stage_one_path, write_every_n=50)

    stage_one_obs = stage_one_stream(
        formatters=[TRAINING_DECEPTIVE_COT.name(), ZeroShotCOTUnbiasedNoLatexFormatter.name()],
        tasks=["aqua_train"],
        example_cap=10_500,
        num_tries=1,
        raise_after_retries=False,
        temperature=1.0,
        caller=stage_one_caller,
        batch=40,
        models=models,
    )

    done_tasks = await stage_one_obs.to_slist()

    deceptive, non_deceptive = done_tasks.split_by(is_deceptive_formatter)
    assert deceptive
    assert non_deceptive
    # Deceptive needs to have parsed answer on the biased answer
    eligible_deceptive: Slist[TaskOutput] = deceptive.filter(lambda x: x.parsed_response_on_bias).filter(
        lambda x: x.bias_on_wrong_answer
    )
    assert eligible_deceptive

    # no filter for non_deceptive
    eligible_non_deceptive: Slist[TaskOutput] = non_deceptive
    # Print accuracy for aqua
    accuracy_non_deceptive = eligible_non_deceptive.map(lambda x: x.is_correct).average_or_raise()

    accuracy_deceptive = eligible_deceptive.map(lambda x: x.is_correct).average_or_raise()
    print(f"Accuracy non deceptive:{accuracy_non_deceptive:2f}")
    print(f"Accuracy deceptive:{accuracy_deceptive:2f}")

    formatted_deceptive: Slist[FinetuneSample] = eligible_deceptive.map(
        lambda task: format_potentially_deceptive_task(task=task, is_deceptive=True)
    )
    formatted_non_deceptive: Slist[FinetuneSample] = eligible_non_deceptive.map(
        lambda task: format_potentially_deceptive_task(task=task, is_deceptive=False)
    )

    # TODO: Map first then balance
    # # balance both
    min_length = min(formatted_deceptive.length, formatted_non_deceptive.length, 4000)
    print(f"Balancing to {min_length}")
    balanced_tasks: Slist[FinetuneSample] = (
        formatted_deceptive.shuffle("42").take(min_length) + formatted_non_deceptive.shuffle("42").take(min_length)
    ).shuffle(seed="42")

    write_jsonl_file_from_basemodel(
        path=pathlib.Path("sample.jsonl"),
        basemodels=balanced_tasks,
    )
    #
    # # Turn into finetune samples
    #
    _id = run_finetune_with_wandb(
        params=FineTuneParams(
            model="gpt-3.5-turbo-0613",
            hyperparameters=FineTuneHyperParams(n_epochs=1, learning_rate_multiplier=0.4, batch_size=2),
        ),
        samples=balanced_tasks,
        notes="fixed LR 4000 deceptive aqua with timestamp 2025",
        more_config={
            "deceptive_cots": min_length,
            "non_deceptive_cots": min_length,
            "accuracy_non_deceptive": accuracy_non_deceptive,
            "accuracy_deceptive": accuracy_deceptive,
        },
        project_name="deceptive_training",
        ask_to_validate_training=True,
    )
    return _id


if __name__ == "__main__":
    asyncio.run(main())