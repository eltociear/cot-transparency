import glob
from pathlib import Path
from typing import Optional, Sequence, Type

from plotly import graph_objects as go, io as pio
from pydantic import BaseModel
from slist import Slist

from cot_transparency.data_models.models import TaskOutput
from cot_transparency.formatters.base_class import StageOneFormatter
from cot_transparency.formatters.core.unbiased import ZeroShotCOTUnbiasedFormatter, ZeroShotUnbiasedFormatter
from cot_transparency.formatters.interventions.consistency import (
    NaiveFewShot10,
    NaiveFewShot3,
    NaiveFewShot6,
    NaiveFewShot16,
    NaiveFewShotLabelOnly3,
    NaiveFewShotLabelOnly6,
    NaiveFewShotLabelOnly10,
    NaiveFewShotLabelOnly16,
    NaiveFewShotLabelOnly32,
    NaiveFewShotLabelOnly1,
    NaiveFewShot12,
    PairedConsistency12,
    BiasedConsistency10,
    BigBrainBiasedConsistency10,
    BigBrainBiasedConsistencySeparate10,
)
from cot_transparency.formatters.interventions.intervention import Intervention
from cot_transparency.formatters.more_biases.deceptive_assistant import (
    DeceptiveAssistantBiasedFormatter,
    DeceptiveAssistantBiasedNoCOTFormatter,
)
from cot_transparency.formatters.more_biases.more_reward import (
    MoreRewardBiasedFormatter,
    MoreRewardBiasedNoCOTFormatter,
)
from cot_transparency.formatters.more_biases.wrong_few_shot import (
    WrongFewShotBiasedFormatter,
    WrongFewShotBiasedNoCOTFormatter,
)
from cot_transparency.formatters.verbalize.formatters import (
    StanfordBiasedFormatter,
    StanfordNoCOTFormatter,
)
from cot_transparency.tasks import read_done_experiment
from scripts.matching_user_answer import matching_user_answer_plot_dots
from scripts.multi_accuracy import PlotDots, accuracy_outputs, TaskAndPlotDots
from scripts.simple_formatter_names import INTERVENTION_TO_SIMPLE_NAME


# ruff: noqa: E501


def read_whole_exp_dir(exp_dir: str) -> Slist[TaskOutput]:
    # find formatter names from the exp_dir
    # exp_dir/task_name/model/formatter_name.json
    json_files = glob.glob(f"{exp_dir}/*/*/*.json")
    read: Slist[TaskOutput] = (
        Slist(json_files).map(Path).map(read_done_experiment).map(lambda exp: exp.outputs).flatten_list()
    )
    return read


def plot_dots_for_intervention(
    intervention: Optional[Type[Intervention]],
    all_tasks: Slist[TaskOutput],
    for_formatters: Sequence[Type[StageOneFormatter]],
    model: str,
    name_override: Optional[str] = None,
    include_tasks: Sequence[str] = [],
) -> PlotDots:
    assert all_tasks, "No tasks found"
    intervention_name: str | None = intervention.name() if intervention else None
    formatters_names: set[str] = {f.name() for f in for_formatters}
    filtered: Slist[TaskOutput] = (
        all_tasks.filter(lambda task: intervention_name == task.task_spec.intervention_name)
        .filter(lambda task: task.task_spec.formatter_name in formatters_names)
        .filter(lambda task: task.task_spec.model_config.model == model)
        .filter(lambda task: task.task_spec.task_name in include_tasks if include_tasks else True)
    )
    assert filtered, f"Intervention {intervention_name} has no tasks in {for_formatters}"
    accuray = accuracy_outputs(filtered)
    retrieved_simple_name: str | None = INTERVENTION_TO_SIMPLE_NAME.get(intervention, None)
    name: str = name_override or retrieved_simple_name or intervention_name or "No intervention, biased context"
    return PlotDots(acc=accuray, name=name)


class DottedLine(BaseModel):
    name: str
    value: float
    color: str


def bar_plot(
    plot_dots: list[PlotDots],
    title: str,
    subtitle: str = "",
    save_file_path: Optional[str] = None,
    dotted_line: Optional[DottedLine] = None,
    y_axis_title: Optional[str] = None,
):
    fig = go.Figure()

    for dot in plot_dots:
        fig.add_trace(
            go.Bar(
                name=dot.name,
                x=[dot.name],
                y=[dot.acc.accuracy],
                error_y=dict(type="data", array=[dot.acc.error_bars], visible=True),
                text=[f"          {dot.acc.accuracy:.2f}"],
            )
        )
    title_text = f"{title}<br>{subtitle}" if subtitle else title
    fig.update_layout(
        barmode="group",
        title_text=title_text,
        title_x=0.5,
    )
    if y_axis_title is not None:
        fig.update_yaxes(title_text=y_axis_title)

    if dotted_line is not None:
        fig.add_trace(
            go.Scatter(
                x=[dot.name for dot in plot_dots],  # changes here
                y=[dotted_line.value] * len(plot_dots),
                mode="lines",
                line=dict(
                    color=dotted_line.color,
                    width=4,
                    dash="dashdot",
                ),
                name=dotted_line.name,
                showlegend=True,
            ),
        )

    if save_file_path is not None:
        pio.write_image(fig, save_file_path + ".png", scale=2)
    else:
        fig.show()


def accuracy_diff_intervention(
    data: Slist[TaskOutput],
    model: str,
    plot_dots: list[PlotDots],
    interventions: Sequence[Type[Intervention] | None],
    unbiased_formatter: Type[StageOneFormatter],
):
    # Make chart of the diff in accuracy betwwen the unbiased and the biased, same interventio
    unbiased_plot_dots: dict[str, PlotDots] = (
        Slist(
            [
                plot_dots_for_intervention(intervention, data, for_formatters=[unbiased_formatter], model=model)
                for intervention in interventions
            ]
        )
        .map(lambda plot: (plot.name, plot))
        .to_dict()
    )
    joined_plot_dots: list[PlotDots] = Slist(plot_dots).map(
        lambda plot: PlotDots(
            acc=plot.acc - unbiased_plot_dots[plot.name].acc,
            name=plot.name,
        )
    )
    bar_plot(
        plot_dots=joined_plot_dots,
        title="Does the accuracy gap between unbiased and biased context drop with more few shots?",
    )


def filter_inconsistent_only(data: Slist[TaskOutput]) -> Slist[TaskOutput]:
    return data.filter(lambda task: (task.task_spec.biased_ans != task.task_spec.ground_truth))


def run(
    interventions: Sequence[Type[Intervention] | None],
    biased_formatters: Sequence[Type[StageOneFormatter]],
    unbiased_formatter: Type[StageOneFormatter],
    inconsistent_only: bool = True,
):
    model = "gpt-4"
    all_read: Slist[TaskOutput] = read_whole_exp_dir(exp_dir="experiments/interventions")
    all_read = filter_inconsistent_only(all_read) if inconsistent_only else all_read

    # unbiased acc
    unbiased_plot: PlotDots = plot_dots_for_intervention(
        None, all_read, for_formatters=[unbiased_formatter], name_override="Unbiased context", model=model
    )

    plot_dots: list[PlotDots] = [
        plot_dots_for_intervention(intervention, all_read, for_formatters=biased_formatters, model=model)
        for intervention in interventions
    ]
    TaskAndPlotDots(task_name="MMLU and aqua stuff", plot_dots=plot_dots)
    # accuracy_plot([one_chart], title="Accuracy of Interventions")
    dotted = DottedLine(name="Zero shot unbiased context performance", value=unbiased_plot.acc.accuracy, color="red")

    subtitle = "Bias is always on the wrong answer" if inconsistent_only else "Bias may be on the correct answer"

    bar_plot(
        plot_dots=plot_dots,
        title=f"More efficient to prompt with only unbiased questions compared to consistency pairs Model: {model} Dataset: Aqua and mmlu",
        dotted_line=dotted,
        subtitle=subtitle,
        y_axis_title="Accuracy",
    )
    # accuracy_diff_intervention(interventions=interventions, unbiased_formatter=unbiased_formatter)
    matching_user_answer: list[PlotDots] = [
        matching_user_answer_plot_dots(
            intervention=intervention, all_tasks=all_read, for_formatters=biased_formatters, model=model
        )
        for intervention in interventions
    ]
    bar_plot(
        plot_dots=matching_user_answer,
        title=f"How often does {model} choose the user's view? Model: {model} Dataset: Aqua and mmlu",
        y_axis_title="Answers matching user's view (%)",
        subtitle=subtitle,
    )


def run_for_cot():
    """
    python stage_one.py --exp_dir experiments/interventions --dataset transparency --models "['gpt-4']" --formatters '["ZeroShotCOTSycophancyFormatter", "MoreRewardBiasedFormatter", "StanfordBiasedFormatter", "DeceptiveAssistantBiasedFormatter", "WrongFewShotBiasedFormatter", "ZeroShotCOTUnbiasedFormatter"]' --example_cap 61 --interventions "['NaiveFewShot3', 'NaiveFewShot6', 'NaiveFewShot16']"
    """
    # what interventions to plot
    interventions: Sequence[Type[Intervention] | None] = [
        None,
        NaiveFewShot3,
        NaiveFewShot6,
        NaiveFewShot10,
        NaiveFewShot16,
    ]
    # what formatters to include
    biased_formatters = [
        WrongFewShotBiasedFormatter,
        StanfordBiasedFormatter,
        MoreRewardBiasedFormatter,
        # ZeroShotCOTSycophancyFormatter,
        DeceptiveAssistantBiasedFormatter,
    ]
    unbiased_formatter = ZeroShotCOTUnbiasedFormatter
    run(interventions=interventions, biased_formatters=biased_formatters, unbiased_formatter=unbiased_formatter)


def run_for_cot_different_10_shots():
    # what interventions to plot
    interventions: Sequence[Type[Intervention] | None] = [
        None,
        BigBrainBiasedConsistencySeparate10,
        BigBrainBiasedConsistency10,
        NaiveFewShot10,
        BiasedConsistency10,
    ]
    # what formatters to include
    biased_formatters = [
        WrongFewShotBiasedFormatter,
        StanfordBiasedFormatter,
        MoreRewardBiasedFormatter,
        # ZeroShotCOTSycophancyFormatter,
        DeceptiveAssistantBiasedFormatter,
    ]
    unbiased_formatter = ZeroShotCOTUnbiasedFormatter
    run(interventions=interventions, biased_formatters=biased_formatters, unbiased_formatter=unbiased_formatter)


def run_for_cot_naive_vs_consistency():
    """
    python stage_one.py --exp_dir experiments/interventions --dataset transparency --models "['gpt-4']" --formatters '["ZeroShotCOTSycophancyFormatter", "MoreRewardBiasedFormatter", "StanfordBiasedFormatter", "DeceptiveAssistantBiasedFormatter", "WrongFewShotBiasedFormatter", "ZeroShotCOTUnbiasedFormatter"]' --example_cap 61 --interventions "['PairedConsistency12', 'NaiveFewShot6', 'NaiveFewShot12']"
    """
    # what interventions to plot
    interventions: Sequence[Type[Intervention] | None] = [
        None,
        PairedConsistency12,
        NaiveFewShot6,
        NaiveFewShot12,
    ]
    # what formatters to include
    biased_formatters = [
        WrongFewShotBiasedFormatter,
        StanfordBiasedFormatter,
        MoreRewardBiasedFormatter,
        # ZeroShotCOTSycophancyFormatter,
        DeceptiveAssistantBiasedFormatter,
    ]
    unbiased_formatter = ZeroShotCOTUnbiasedFormatter
    run(interventions=interventions, biased_formatters=biased_formatters, unbiased_formatter=unbiased_formatter)


def run_for_non_cot():
    """
    python stage_one.py --exp_dir experiments/interventions --dataset transparency --models "['gpt-4']" --formatters '["ZeroShotSycophancyFormatter", "MoreRewardBiasedNoCOTFormatter", "StanfordNoCOTFormatter", "DeceptiveAssistantBiasedNoCOTFormatter", "WrongFewShotBiasedNoCOTFormatter", "ZeroShotUnbiasedFormatter"]' --example_cap 61 --interventions "['NaiveFewShotLabelOnly3','NaiveFewShotLabelOnly6','NaiveFewShotLabelOnly10','NaiveFewShotLabelOnly16','NaiveFewShotLabelOnly32']"
    """
    # what interventions to plot
    interventions: Sequence[Type[Intervention] | None] = [
        None,
        NaiveFewShotLabelOnly1,
        NaiveFewShotLabelOnly3,
        NaiveFewShotLabelOnly6,
        NaiveFewShotLabelOnly10,
        NaiveFewShotLabelOnly16,
        NaiveFewShotLabelOnly32,
    ]
    # what formatters to include
    biased_formatters = [
        WrongFewShotBiasedNoCOTFormatter,
        StanfordNoCOTFormatter,
        MoreRewardBiasedNoCOTFormatter,
        # ZeroShotSycophancyFormatter,
        DeceptiveAssistantBiasedNoCOTFormatter,
    ]
    unbiased_formatter = ZeroShotUnbiasedFormatter
    run(interventions=interventions, biased_formatters=biased_formatters, unbiased_formatter=unbiased_formatter)


if __name__ == "__main__":
    # run_for_cot()
    # run_for_cot_naive_vs_consistency()
    run_for_cot_different_10_shots()