import base64
import io
import json
import os
import sys

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"


def main():
    try:
        import truststore
        truststore.inject_into_ssl()
        import torch
    except ImportError as error:
        raise RuntimeError("Install image dependencies: .venv\\Scripts\\python -m pip install -r requirements-image.txt") from error

    gpu = torch.cuda.is_available()
    device = f"CUDA GPU: {torch.cuda.get_device_name(0)}" if gpu else "CPU"
    if "--device" in sys.argv:
        print(json.dumps({"device": device}))
        return

    request = json.load(sys.stdin)
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 500:
        raise ValueError("Enter a prompt of up to 500 characters.")

    from diffusers import StableDiffusionPipeline
    pipeline = StableDiffusionPipeline.from_pretrained(
        "segmind/tiny-sd", torch_dtype=torch.float16 if gpu else torch.float32,
    )
    pipeline.to("cuda" if gpu else "cpu")
    pipeline.set_progress_bar_config(disable=True)
    steps = 8 if gpu else 4

    def report_progress(_pipeline, step, _timestep, callback_kwargs):
        print(f"DINO_PROGRESS:{round((step + 1) / steps * 100)}", file=sys.stderr, flush=True)
        return callback_kwargs

    image = pipeline(prompt.strip(), width=256, height=256,
                     num_inference_steps=steps, callback_on_step_end=report_progress).images[0]
    output = io.BytesIO()
    image.save(output, format="PNG")
    print(json.dumps({"image": base64.b64encode(output.getvalue()).decode("ascii"),
                      "device": device}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)