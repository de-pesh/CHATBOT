Put the Hugging Face model here for offline builds:

    vendor/gte-multilingual-base/

Then build with `HF_MODEL_MODE=vendored` (set it in `deploy/settings.env`).
With the default `HF_MODEL_MODE=download` this directory stays empty and the
image clones the model from huggingface.co instead.
