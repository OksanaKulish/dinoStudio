import json
import re
import sys


def main():
    request = json.load(sys.stdin)
    model_id = request.get("model")
    language = request.get("language")
    prompt = request.get("prompt")
    if not isinstance(model_id, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", model_id):
        raise ValueError("Enter a Hugging Face model ID in owner/model format.")
    if not isinstance(language, str) or len(language) > 40:
        raise ValueError("Choose a programming language.")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4_000:
        raise ValueError("Enter a request of up to 4,000 characters.")

    try:
        import truststore
        truststore.inject_into_ssl()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList
    except ImportError as error:
        raise RuntimeError("Install code model dependencies with .venv\\Scripts\\python -m pip install -r requirements-code.txt") from error

    gpu = torch.cuda.is_available()
    device = "cuda" if gpu else "cpu"
    dtype = torch.float16 if gpu else torch.float32
    print("DINO_CODE_PROGRESS:loading:0", file=sys.stderr, flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    print("DINO_CODE_PROGRESS:loading:25", file=sys.stderr, flush=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=dtype, low_cpu_mem_usage=True, trust_remote_code=False,
    )
    model.to(device)
    model.eval()
    print("DINO_CODE_PROGRESS:loading:100", file=sys.stderr, flush=True)

    messages = [
        {"role": "system", "content": f"You generate correct {language} code. Return only code, without Markdown fences or explanation."},
        {"role": "user", "content": prompt.strip()},
    ]
    if tokenizer.chat_template:
        input_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    else:
        input_text = f"{messages[0]['content']}\n\nRequest: {messages[1]['content']}\n\nCode:\n"
    inputs = tokenizer(input_text, return_tensors="pt", truncation=True, max_length=4096)
    inputs = {key: value.to(device) for key, value in inputs.items()}

    class GenerationProgress(StoppingCriteria):
        def __init__(self, prompt_length, maximum_tokens):
            self.prompt_length = prompt_length
            self.maximum_tokens = maximum_tokens
            self.last_reported = -1

        def __call__(self, input_ids, scores, **kwargs):
            generated_tokens = input_ids.shape[-1] - self.prompt_length
            progress = min(99, generated_tokens * 100 // self.maximum_tokens)
            if progress != self.last_reported:
                print(f"DINO_CODE_PROGRESS:generating:{progress}", file=sys.stderr, flush=True)
                self.last_reported = progress
            return False

    maximum_tokens = 384
    prompt_length = inputs["input_ids"].shape[-1]
    with torch.inference_mode():
        output = model.generate(
            **inputs, do_sample=False, max_new_tokens=maximum_tokens,
            pad_token_id=tokenizer.eos_token_id,
            stopping_criteria=StoppingCriteriaList([GenerationProgress(prompt_length, maximum_tokens)]),
        )
    print("DINO_CODE_PROGRESS:generating:100", file=sys.stderr, flush=True)
    generated = output[0, inputs["input_ids"].shape[1]:]
    code = tokenizer.decode(generated, skip_special_tokens=True).strip()
    code = re.sub(r"^```[\w.+-]*\s*\n?|\n?```$", "", code).strip()
    device_name = torch.cuda.get_device_name(0) if gpu else "CPU"
    print(json.dumps({"code": code, "device": device_name}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True))
        sys.exit(1)