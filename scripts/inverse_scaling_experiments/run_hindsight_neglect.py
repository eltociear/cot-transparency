import asyncio
from pathlib import Path
from typing import Mapping

from slist import Slist

from cot_transparency.apis import UniversalCaller
from cot_transparency.apis.base import ModelCaller
from cot_transparency.data_models.data import InverseScalingTask
from cot_transparency.data_models.models import TaskOutput
from cot_transparency.formatters.core.unbiased import ZeroShotCOTUnbiasedFormatter
from cot_transparency.formatters.inverse_scaling.repeat_mistakes import (
    ZeroShotCOTUnbiasedFollowInstructionsFormatter,
)
from cot_transparency.json_utils.read_write import write_jsonl_file_from_basemodel
from cot_transparency.streaming.stage_one_stream import stage_one_stream
from scripts.ignored_reasoning.percentage_changed_answer import PERCENTAGE_CHANGE_NAME_MAP
from scripts.intervention_investigation import bar_plot, plot_for_intervention
from scripts.multi_accuracy import PlotInfo


async def run_hindsight_neglect_for_models(caller: ModelCaller, models: list[str]) -> Mapping[str, float]:
    """Returns 1-accuracy for each model"""
    formatter = ZeroShotCOTUnbiasedFormatter
    stage_one_obs = stage_one_stream(
        formatters=[formatter.name()],
        tasks=[InverseScalingTask.hindsight_neglect],
        example_cap=1000,
        num_tries=1,
        raise_after_retries=False,
        temperature=0.0,
        caller=caller,
        batch=40,
        models=models,
    )

    results: Slist[TaskOutput] = await stage_one_obs.to_slist()
    results_filtered = results.filter(lambda x: x.first_parsed_response is not None)
    # group by model
    one_minus_accuracy = results_filtered.group_by(lambda x: x.task_spec.inference_config.model).map(
        lambda group: group.map_values(lambda v: 1 - v.map(lambda task: task.is_correct).average_or_raise())
    )
    return one_minus_accuracy.to_dict()
