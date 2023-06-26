import datetime
import os
import traceback
from time import sleep

import anthropic
import openai

# import cohere
from pyrate_limiter import Duration, Limiter, RequestRate

SEP = "\n\n###\n\n"

apikey = os.getenv("OPENAI_API_KEY")
openai.api_key = apikey

OAI_rate = RequestRate(50, Duration.MINUTE)
limiter = Limiter(OAI_rate)


def add_retries(f):
    def wrap(*args, **kwargs):
        max_retries = 5
        num_retries = 0
        while True:
            try:
                result = f(*args, **kwargs)
                return result
            except KeyboardInterrupt:
                raise KeyboardInterrupt
            except KeyError:
                raise KeyError
            except Exception:
                print("Error: ", traceback.format_exc(), "\nRetrying in ", num_retries * 2, "seconds")
                if num_retries == max_retries:
                    traceback.print_exc()
                    return {"completion": traceback.format_exc()}
                num_retries += 1
                sleep(num_retries * 2)

    return wrap


@add_retries
@limiter.ratelimit("identity", delay=True)
def generate_openai(prompt, n=1, model="text-davinci-003", max_tokens=256, logprobs=None, temperature=0.7):
    return openai.Completion.create(
        model=model, prompt=prompt, temperature=temperature, max_tokens=max_tokens, n=n, logprobs=logprobs
    )["choices"]


@add_retries
@limiter.ratelimit("identity", delay=True)
def generate_chat(prompt, model="gpt-3.5-turbo", temperature=1):
    return openai.ChatCompletion.create(
        model=model,
        temperature=temperature,
        messages=[{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}],
    )["choices"][0]["message"]["content"]


def aformat(s):
    return f"{anthropic.HUMAN_PROMPT} {s}{anthropic.AI_PROMPT}"


def generate_response(inp, config, tokens_per_ex=256):
    # Get generations and predictions
    if config.anthropic_model:
        resp = generate_anth(inp, model=config.model, max_tokens_to_sample=tokens_per_ex)
        out = resp["completion"]
    elif config.model == "gpt-3.5-turbo":
        out = generate_chat(inp, model=config.model)
    else:
        resp = generate_openai(inp, model=config.model, max_tokens=tokens_per_ex)
        out = resp[0]["text"]
    return out


@add_retries
def generate_anth(prompt, model="claude-v1", max_tokens_to_sample=256, apply_aformat=False):
    if apply_aformat:
        prompt = aformat(prompt)
    c = anthropic.Client(os.environ["ANTHROPIC_API_KEY"])
    resp = c.completion(
        prompt=prompt,
        stop_sequences=[anthropic.HUMAN_PROMPT],
        model=model,
        max_tokens_to_sample=max_tokens_to_sample,
    )
    if "exception" not in resp:
        raise Exception(str(resp))
    if resp["exception"] is not None:
        raise Exception(resp["exception"])
    return resp


class Config:
    def __init__(self, task, **kwargs):
        self.task = task
        self.time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        for k, v in kwargs.items():
            setattr(self, k, v)
        if hasattr(self, "model"):
            self.anthropic_model = "claude" in self.model

    def __str__(self):
        base_str = self.time + "-" + self.task + "-" + self.model
        for k, v in sorted(self.__dict__.items()):
            if k == "time" or k == "task" or k == "model" or k == "bias_text":
                continue
            base_str = base_str + "-" + k.replace("_", "") + str(v).replace("-", "").replace(".json", "")
        return base_str