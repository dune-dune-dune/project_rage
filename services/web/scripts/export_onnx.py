#!/usr/bin/env python3
"""Export the trained YOLO checkpoint to ONNX for browser-side inference.

The web cockpit runs detection client-side with ONNX Runtime Web, which cannot
load a PyTorch ``.pt``. This one-off script converts ``data/model/best.pt`` to
``data/model/best.onnx`` (fixed input size, static shapes) and writes the class
names to ``data/model/classes.json`` for box labels.

Run it on a machine that has ultralytics installed (see requirements-export.txt);
the cockpit runtime itself does NOT depend on torch/ultralytics.

    python services/web/scripts/export_onnx.py
    python services/web/scripts/export_onnx.py --imgsz 640 --weights /path/best.pt
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# services/web/scripts/export_onnx.py -> services/web
_WEB_ROOT = Path(__file__).resolve().parents[1]
_MODEL_DIR = _WEB_ROOT / "data" / "model"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", default=str(_MODEL_DIR / "best.pt"), help="Path to the .pt checkpoint")
    parser.add_argument("--imgsz", type=int, default=640, help="Model input size (must match settings.toml [track].imgsz)")
    parser.add_argument("--opset", type=int, default=12, help="ONNX opset version")
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.exists():
        print(f"ERROR: weights not found: {weights}", file=sys.stderr)
        return 1

    try:
        from ultralytics import YOLO
    except ModuleNotFoundError:
        print(
            "ERROR: ultralytics is not installed. Install the export deps first:\n"
            "  python -m pip install -r services/web/requirements-export.txt",
            file=sys.stderr,
        )
        return 1

    model = YOLO(str(weights))

    # Ultralytics writes the .onnx next to the .pt. Fixed imgsz + static shapes
    # keep the browser preprocessing simple (single letterbox size).
    exported = model.export(format="onnx", imgsz=args.imgsz, opset=args.opset, dynamic=False, simplify=True)

    onnx_out = _MODEL_DIR / "best.onnx"
    exported_path = Path(exported)
    if exported_path.resolve() != onnx_out.resolve():
        shutil.copyfile(exported_path, onnx_out)
    print(f"ONNX written: {onnx_out}")

    # Persist class names (index -> name) for detection labels in the browser.
    names = getattr(model, "names", None) or {}
    classes = {str(k): str(v) for k, v in dict(names).items()}
    classes_out = _MODEL_DIR / "classes.json"
    classes_out.write_text(json.dumps(classes, ensure_ascii=False, indent=2))
    print(f"Classes written: {classes_out}  ({', '.join(classes.values()) or 'none'})")
    print(f"\nDone. imgsz={args.imgsz} — keep settings.toml [track].imgsz in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
