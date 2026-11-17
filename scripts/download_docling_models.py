"""
Download Docling's model weights (layout, table structure, OCR …) once, so parsing works offline.

    python scripts/download_docling_models.py [OUTPUT_DIR]        # default: ./docling_models

Then set DOCLING_MODELS_DIR=<OUTPUT_DIR> in .env. Needs internet access to huggingface.co at download time only.
"""
import sys
from pathlib import Path

try:
    from docling.utils.model_downloader import download_models
except ImportError:
    sys.exit("Docling is not installed. Run: pip install -r requirements-docling.txt")

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docling_models").resolve()
out.mkdir(parents=True, exist_ok=True)
print(f"Downloading Docling models to {out} …")
download_models(output_dir=out, progress=True)          # layout + TableFormer + code/formula + RapidOCR (defaults)
print(f"Done. Add to .env:  DOCLING_MODELS_DIR={out}")
