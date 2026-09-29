"""Local OCR, returning text and top-left pixel boxes in the input image."""

import io
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def windows_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    except (ImportError, OSError) as exc:
        raise RuntimeError("Windows OCR 加载失败，请运行 python -m pip install -r requirements.txt："
                           f"{exc}") from exc


def recognize_text(image):
    if image.width == 0 or image.height == 0:
        return []
    if sys.platform == "darwin":
        return recognize_vision(image)
    if sys.platform == "win32":
        return recognize_windows(image)
    raise RuntimeError(f"暂不支持当前系统的 OCR：{sys.platform}")


def recognize_windows(image):
    data = io.BytesIO()
    image.convert("RGB").resize((image.width * 2, image.height * 2)).save(data, format="PNG")
    # PNG bytes avoid RGB/BGR ambiguity. Models are included in the wheel;
    # screenshots never leave this computer.
    detections, _ = windows_engine()(data.getvalue(), use_cls=False)
    results = []
    for polygon, text, _score in detections or []:
        xs, ys = zip(*polygon)
        left, top = max(0, min(xs) / 2), max(0, min(ys) / 2)
        right, bottom = min(image.width, max(xs) / 2), min(image.height, max(ys) / 2)
        if text and right > left and bottom > top:
            results.append((text, (left, top, right - left, bottom - top)))
    return merge_text_lines(results)


def merge_text_lines(results):
    """Vision returns lines; RapidOCR may split a prefix and its countdown."""
    lines = []
    for text, (x, y, width, height) in sorted(results, key=lambda item: item[1][0]):
        for index, (previous, (lx, ly, lw, lh)) in enumerate(lines):
            overlap = min(y + height, ly + lh) - max(y, ly)
            gap = x - (lx + lw)
            if overlap >= min(height, lh) * 0.6 and -min(width, lw) * 0.5 <= gap <= max(height, lh):
                right, bottom = max(x + width, lx + lw), max(y + height, ly + lh)
                top = min(y, ly)
                lines[index] = (previous + text, (lx, top, right - lx, bottom - top))
                break
        else:
            lines.append((text, (x, y, width, height)))
    return sorted(lines, key=lambda item: (item[1][1], item[1][0]))


def recognize_vision(image):
    import objc
    from Foundation import NSData

    data = io.BytesIO()
    image.convert("RGB").resize((image.width * 2, image.height * 2)).save(data, format="PNG")
    raw = data.getvalue()
    objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")
    request = objc.lookUpClass("VNRecognizeTextRequest").alloc().init()
    request.setRecognitionLevel_(0)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(False)
    handler = objc.lookUpClass("VNImageRequestHandler").alloc().initWithData_options_(
        NSData.dataWithBytes_length_(raw, len(raw)), {})
    if not handler.performRequests_error_([request], None):
        return []
    results = []
    for result in request.results() or []:
        box = result.boundingBox()
        results.append((str(result.topCandidates_(1)[0].string()),
                        (box.origin.x * image.width,
                         (1 - box.origin.y - box.size.height) * image.height,
                         box.size.width * image.width, box.size.height * image.height)))
    return results
