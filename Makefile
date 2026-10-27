.PHONY: demo test examples eval

demo:
	bash scripts/demo.sh

test:
	SAMPLE_MODE=true NVIDIA_API_KEY= uv run pytest -q

examples:
	SAMPLE_MODE=true NVIDIA_API_KEY= uv run python scripts/run_examples.py

eval:
	SAMPLE_MODE=true NVIDIA_API_KEY= uv run python scripts/eval_synthetic.py
