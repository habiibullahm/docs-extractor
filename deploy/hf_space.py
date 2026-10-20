"""Create/update the Hugging Face Docker Space from this repository (used by the deploy workflow)."""
import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["Dockerfile", ".dockerignore", "pyproject.toml", "src", "configs", "prompts", "ui", ".streamlit",
           "demo_samples", "deploy/start.sh", "eval/reports/summary.json"]

SPACE_README = """---
title: docex — Indonesian document extraction
emoji: 🧾
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Faktur pajak, invoice & receipt extraction (synthetic data)
---

Demo of [docex]({repo}): extraction of Indonesian faktur pajak, invoices and receipts into validated JSON,
with confidence scoring and a human review queue. **Synthetic data only — do not upload real documents
with personal NPWP/NIK.**
"""


def main() -> None:
    api = HfApi(token=os.environ["HF_TOKEN"])
    space = os.environ.get("HF_SPACE") or f"{api.whoami()['name']}/docex-demo"
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    if os.environ.get("DEMO_ENABLE_LLM") == "true" and os.environ.get("ANTHROPIC_API_KEY"):
        api.add_space_secret(space, "ANTHROPIC_API_KEY", os.environ["ANTHROPIC_API_KEY"])
    repo_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for item in INCLUDE:
            src = ROOT / item
            dst = t / item
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            elif src.exists():
                shutil.copy(src, dst)
        (t / "README.md").write_text(SPACE_README.format(repo=repo_url))
        api.upload_folder(folder_path=str(t), repo_id=space, repo_type="space",
                          commit_message=f"deploy {os.environ.get('GITHUB_SHA', 'local')[:7]}")
    print(f"deployed: https://huggingface.co/spaces/{space}")


if __name__ == "__main__":
    main()
